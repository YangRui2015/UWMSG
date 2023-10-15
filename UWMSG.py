from typing import Any, Dict, List, Optional, Tuple, Union
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import os
import random
import uuid

import d4rl
import gym
import numpy as np
import pyrallis
import torch
from torch.distributions import Normal
import torch.nn as nn
from tqdm import trange
import wandb
from configs import get_attack_config, get_UWMSG_config

def asdict(config):
    dic = {}
    config_dict = config.__dict__
    for key, value in config_dict.items():
        if not key.startswith('__'):
            dic[key] = value
    return dic

@dataclass
class TrainConfig:
    # wandb params
    project: str = "Corruption_EDAC"
    group: str = 'MSG' 
    name: str = "MSG"   
    # model params
    hidden_dim: int = 256
    num_critics: int = 10
    gamma: float = 0.99
    tau: float = 5e-3
    actor_learning_rate: float = 3e-4
    critic_learning_rate: float = 3e-4
    alpha_learning_rate: float = 3e-4
    max_action: float = 1.0
    # training params
    buffer_size: int = 1_000_000
    env_name: str = "halfcheetah-medium-v2" 
    batch_size: int = 256
    num_epochs: int = 3000     
    num_updates_on_epoch: int = 1000
    normalize_reward: bool = False
    # evaluation params
    eval_episodes: int = 10 
    eval_every: int = 10  
    # general params
    checkpoints_path: Optional[str] = None 
    deterministic_torch: bool = False
    train_seed: int = 0          
    eval_seed: int = 42
    log_every: int = 100
    device: str = "cuda"
    LCB_ratio: float = 4.0     
    ######## attack
    # the path of the saved model to perform adversarial attack
    corrupt_model_path: str = './log_checpoints/MSG-10_QLCB4_seed0-walker2d-medium-replay-v2-f082528d/2000.pt'
    gradient_attack: bool = False # if true, perform adversarial gradient-based attack on the dynamics
    corruption_reward: bool = False
    corruption_dynamics: bool = False
    random_corruption: bool = False
    corruption_range: float = 30  
    corruption_rate: float = 0.3   
    ######## UW
    use_UW: bool = False
    uncertainty_ratio: float = 0.3
    uncertainty_basic: float = 0.0
    uncertainty_min: float = 1
    uncertainty_max: float = 10


# general utils
TensorBatch = List[torch.Tensor]


def soft_update(target: nn.Module, source: nn.Module, tau: float):
    for target_param, source_param in zip(target.parameters(), source.parameters()):
        target_param.data.copy_((1 - tau) * target_param.data + tau * source_param.data)


def wandb_init(config: dict) -> None:
    wandb.init(
        config=config,
        project=config["project"],
        group=config["group"],
        name=config["name"],
        id=str(uuid.uuid4()),
    )
    wandb.run.save() 

def set_seed(
    seed: int, env: Optional[gym.Env] = None, deterministic_torch: bool = False
):
    if env is not None:
        env.seed(seed)
        env.action_space.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(deterministic_torch)


def wrap_env(
    env: gym.Env,
    state_mean: Union[np.ndarray, float] = 0.0,
    state_std: Union[np.ndarray, float] = 1.0,
    reward_scale: float = 1.0,
) -> gym.Env:
    def normalize_state(state):
        return (state - state_mean) / state_std

    def scale_reward(reward):
        return reward_scale * reward

    env = gym.wrappers.TransformObservation(env, normalize_state)
    if reward_scale != 1.0:
        env = gym.wrappers.TransformReward(env, scale_reward)
    return env


class ReplayBuffer:
    def __init__(
        self,
        state_dim: int,
        action_dim: int,
        buffer_size: int,
        device: str = "cpu",
    ):
        self._buffer_size = buffer_size
        self._pointer = 0
        self._size = 0

        self._states = torch.zeros(
            (buffer_size, state_dim), dtype=torch.float32, device=device
        )
        self._actions = torch.zeros(
            (buffer_size, action_dim), dtype=torch.float32, device=device
        )
        self._rewards = torch.zeros((buffer_size, 1), dtype=torch.float32, device=device)
        self._next_states = torch.zeros(
            (buffer_size, state_dim), dtype=torch.float32, device=device
        )
        self._dones = torch.zeros((buffer_size, 1), dtype=torch.float32, device=device)
        self._device = device

    def _to_tensor(self, data: np.ndarray) -> torch.Tensor:
        return torch.tensor(data, dtype=torch.float32, device=self._device)

    # Loads data in d4rl format, i.e. from Dict[str, np.array].
    def load_d4rl_dataset(self, data: Dict[str, np.ndarray]):
        if self._size != 0:
            raise ValueError("Trying to load data into non-empty replay buffer")
        n_transitions = data["observations"].shape[0]
        if n_transitions > self._buffer_size:
            raise ValueError(
                "Replay buffer is smaller than the dataset you are trying to load!"
            )
        self._states[:n_transitions] = self._to_tensor(data["observations"])
        self._actions[:n_transitions] = self._to_tensor(data["actions"])
        self._rewards[:n_transitions] = self._to_tensor(data["rewards"][..., None])
        self._next_states[:n_transitions] = self._to_tensor(data["next_observations"])
        self._dones[:n_transitions] = self._to_tensor(data["terminals"][..., None])
        self._size += n_transitions
        self._pointer = min(self._size, n_transitions)
        print(f"Dataset size: {n_transitions}")

    def sample(self, batch_size: int) -> TensorBatch:
        indices = np.random.randint(0, min(self._size, self._pointer), size=batch_size)
        states = self._states[indices]
        actions = self._actions[indices]
        rewards = self._rewards[indices]
        next_states = self._next_states[indices]
        dones = self._dones[indices]
        return [states, actions, rewards, next_states, dones]

    def add_transition(self):
        # Use this method to add new data into the replay buffer during fine-tuning.
        raise NotImplementedError


# SAC Actor & Critic implementation
class VectorizedLinear(nn.Module):
    def __init__(self, in_features: int, out_features: int, ensemble_size: int):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.ensemble_size = ensemble_size

        self.weight = nn.Parameter(torch.empty(ensemble_size, in_features, out_features))
        self.bias = nn.Parameter(torch.empty(ensemble_size, 1, out_features))

        self.reset_parameters()

    def reset_parameters(self):
        # default pytorch init for nn.Linear module
        for layer in range(self.ensemble_size):
            nn.init.kaiming_uniform_(self.weight[layer], a=math.sqrt(5))

        fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight[0])
        bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
        nn.init.uniform_(self.bias, -bound, bound)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # input: [ensemble_size, batch_size, input_size]
        # weight: [ensemble_size, input_size, out_size]
        # out: [ensemble_size, batch_size, out_size]
        return x @ self.weight + self.bias


class Actor(nn.Module):
    def __init__(
        self, state_dim: int, action_dim: int, hidden_dim: int, max_action: float = 1.0
    ):
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        # with separate layers works better than with Linear(hidden_dim, 2 * action_dim)
        self.mu = nn.Linear(hidden_dim, action_dim)
        self.log_sigma = nn.Linear(hidden_dim, action_dim)

        # init as in the EDAC paper
        for layer in self.trunk[::2]:
            torch.nn.init.constant_(layer.bias, 0.1)

        torch.nn.init.uniform_(self.mu.weight, -1e-3, 1e-3)
        torch.nn.init.uniform_(self.mu.bias, -1e-3, 1e-3)
        torch.nn.init.uniform_(self.log_sigma.weight, -1e-3, 1e-3)
        torch.nn.init.uniform_(self.log_sigma.bias, -1e-3, 1e-3)

        self.action_dim = action_dim
        self.max_action = max_action

    def forward(
        self,
        state: torch.Tensor,
        deterministic: bool = False,
        need_log_prob: bool = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        hidden = self.trunk(state)
        mu, log_sigma = self.mu(hidden), self.log_sigma(hidden)

        # clipping params from EDAC paper, not as in SAC paper (-20, 2)
        log_sigma = torch.clip(log_sigma, -5, 2)
        policy_dist = Normal(mu, torch.exp(log_sigma))

        if deterministic:
            action = mu
        else:
            action = policy_dist.rsample()

        tanh_action, log_prob = torch.tanh(action), None
        if need_log_prob:
            # change of variables formula (SAC paper, appendix C, eq 21)
            log_prob = policy_dist.log_prob(action).sum(axis=-1)
            log_prob = log_prob - torch.log(1 - tanh_action.pow(2) + 1e-6).sum(axis=-1)

        return tanh_action * self.max_action, log_prob

    @torch.no_grad()
    def act(self, state: np.ndarray, device: str) -> np.ndarray:
        deterministic = not self.training
        state = torch.tensor(state, device=device, dtype=torch.float32)
        action = self(state, deterministic=deterministic)[0].cpu().numpy()
        return action


class VectorizedCritic(nn.Module):
    def __init__(
        self, state_dim: int, action_dim: int, hidden_dim: int, num_critics: int
    ):
        super().__init__()
        self.critic = nn.Sequential(
            VectorizedLinear(state_dim + action_dim, hidden_dim, num_critics),
            nn.ReLU(),
            VectorizedLinear(hidden_dim, hidden_dim, num_critics),
            nn.ReLU(),
            VectorizedLinear(hidden_dim, hidden_dim, num_critics),
            nn.ReLU(),
            VectorizedLinear(hidden_dim, 1, num_critics),
        )
        # init as in the EDAC paper
        for layer in self.critic[::2]:
            torch.nn.init.constant_(layer.bias, 0.1)

        torch.nn.init.uniform_(self.critic[-1].weight, -3e-3, 3e-3)
        torch.nn.init.uniform_(self.critic[-1].bias, -3e-3, 3e-3)

        self.num_critics = num_critics

    def forward(self, state: torch.Tensor, action: torch.Tensor) -> torch.Tensor:
        # [batch_size, state_dim + action_dim]
        state_action = torch.cat([state, action], dim=-1)
        # [num_critics, batch_size, state_dim + action_dim]
        state_action = state_action.unsqueeze(0).repeat_interleave(
            self.num_critics, dim=0
        )
        # [num_critics, batch_size]
        q_values = self.critic(state_action).squeeze(-1)
        return q_values


class SACN:
    def __init__(
        self,
        actor: Actor,
        actor_optimizer: torch.optim.Optimizer,
        critic: VectorizedCritic,
        critic_optimizer: torch.optim.Optimizer,
        gamma: float = 0.99,
        tau: float = 0.005,
        alpha_learning_rate: float = 1e-4,
        LCB_ratio: float = 4.0,
        use_UW: bool = False,
        uncertainty_ratio: float = 1,
        uncertainty_basic: float = 1.0,
        uncertainty_min: float = 1,
        uncertainty_max: float = np.infty,
        device: str = "cpu",  # noqa
    ):
        self.device = device

        self.actor = actor
        self.critic = critic
        with torch.no_grad():
            self.target_critic = deepcopy(self.critic)

        self.actor_optimizer = actor_optimizer
        self.critic_optimizer = critic_optimizer

        self.tau = tau
        self.gamma = gamma
        self.LCB_ratio = LCB_ratio

        # uncertainty weight
        self.use_UW = use_UW
        self.uncertainty_ratio = uncertainty_ratio
        self.uncertainty_basic = uncertainty_basic
        self.uncertainty_min = uncertainty_min
        self.uncertainty_max = uncertainty_max
        self.uncertainty = torch.ones((1,1))

        # adaptive alpha setup
        self.target_entropy = -float(self.actor.action_dim)
        self.log_alpha = torch.tensor(
            [0.0], dtype=torch.float32, device=self.device, requires_grad=True
        )
        self.alpha_optimizer = torch.optim.Adam([self.log_alpha], lr=alpha_learning_rate)
        self.alpha = self.log_alpha.exp().detach()

    def _alpha_loss(self, state: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            action, action_log_prob = self.actor(state, need_log_prob=True)

        loss = (-self.log_alpha * (action_log_prob + self.target_entropy)).mean()

        return loss

    def _actor_loss(self, state: torch.Tensor, action_old: torch.Tensor) -> Tuple[torch.Tensor, float, float]:
        action, action_log_prob = self.actor(state, need_log_prob=True)
        q_value_dist = self.critic(state, action)
        assert q_value_dist.shape[0] == self.critic.num_critics
        q_value_min = q_value_dist.mean(0).view(1, -1) - self.LCB_ratio * q_value_dist.std(0).view(1, -1)
        # needed for logging
        q_value_std = q_value_dist.std(0).mean().item()
        batch_entropy = -action_log_prob.mean().item()
        loss = (self.alpha * action_log_prob.view(1, -1) - q_value_min).mean() 
        return loss, batch_entropy, q_value_std

    def _critic_loss(
        self,
        state: torch.Tensor,
        action: torch.Tensor,
        reward: torch.Tensor,
        next_state: torch.Tensor,
        done: torch.Tensor,
    ) -> torch.Tensor:
        with torch.no_grad():
            next_action, next_action_log_prob = self.actor(
                next_state, need_log_prob=True
            )
            q_next = self.target_critic(next_state, next_action)
            q_next = q_next - self.alpha * next_action_log_prob.view(1,-1)
            q_target = reward.view(1,-1) + self.gamma * (1 - done.view(1,-1)) * q_next.detach()

        q_values = self.critic(state, action)
        # [ensemble_size, batch_size] - [1, batch_size]
        if self.use_UW:
            self.uncertainty = torch.clip(self.uncertainty_basic + self.uncertainty_ratio * q_values.std(dim=0).view(1,-1).detach(), self.uncertainty_min, self.uncertainty_max)
            loss = ((q_values - q_target) ** 2 / self.uncertainty).mean(dim=1).sum(dim=0)
        else:
            loss = ((q_values - q_target) ** 2).mean(dim=1).sum(dim=0)
        return loss

    def update(self, batch: TensorBatch) -> Dict[str, float]:
        state, action, reward, next_state, done = [arr.to(self.device) for arr in batch]

        # Alpha update
        alpha_loss = self._alpha_loss(state)
        self.alpha_optimizer.zero_grad()
        alpha_loss.backward()
        self.alpha_optimizer.step()

        self.alpha = self.log_alpha.exp().detach()

        # Actor update
        actor_loss, actor_batch_entropy, q_policy_std = self._actor_loss(state, action)
        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        self.actor_optimizer.step()

        # Critic update
        critic_loss = self._critic_loss(state, action, reward, next_state, done)
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()

        #  Target networks soft update
        with torch.no_grad():
            soft_update(self.target_critic, self.critic, tau=self.tau)
            # for logging, Q-ensemble std estimate with the random actions:
            # a ~ U[-max_action, max_action]
            max_action = self.actor.max_action
            random_actions = -max_action + 2 * max_action * torch.rand_like(action)

            q_random_std = self.critic(state, random_actions).std(0).mean().item()

        update_info = {
            "alpha_loss": alpha_loss.item(),
            "critic_loss": critic_loss.item(),
            "actor_loss": actor_loss.item(),
            "batch_entropy": actor_batch_entropy,
            "alpha": self.alpha.item(),
            "q_policy_std": q_policy_std,
            "q_random_std": q_random_std,
        }
        return update_info

    def state_dict(self) -> Dict[str, Any]:
        state = {
            "actor": self.actor.state_dict(),
            "critic": self.critic.state_dict(),
            "target_critic": self.target_critic.state_dict(),
            "log_alpha": self.log_alpha.item(),
            "actor_optim": self.actor_optimizer.state_dict(),
            "critic_optim": self.critic_optimizer.state_dict(),
            "alpha_optim": self.alpha_optimizer.state_dict(),
        }
        return state

    def load_state_dict(self, state_dict: Dict[str, Any]):
        self.actor.load_state_dict(state_dict["actor"])
        self.critic.load_state_dict(state_dict["critic"])
        self.target_critic.load_state_dict(state_dict["target_critic"])
        self.actor_optimizer.load_state_dict(state_dict["actor_optim"])
        self.critic_optimizer.load_state_dict(state_dict["critic_optim"])
        self.alpha_optimizer.load_state_dict(state_dict["alpha_optim"])
        self.log_alpha.data[0] = state_dict["log_alpha"]
        self.alpha = self.log_alpha.exp().detach()


@torch.no_grad()
def eval_actor(
    env: gym.Env, actor: Actor, device: str, n_episodes: int, seed: int
) -> np.ndarray:
    env.seed(seed)
    actor.eval()
    episode_rewards = []
    for _ in range(n_episodes):
        state, done = env.reset(), False
        episode_reward = 0.0
        while not done:
            action = actor.act(state, device)
            state, reward, done, _ = env.step(action)
            episode_reward += reward
        episode_rewards.append(episode_reward)

    actor.train()
    return np.array(episode_rewards)


def return_reward_range(dataset, max_episode_steps):
    returns, lengths = [], []
    ep_ret, ep_len = 0.0, 0
    for r, d in zip(dataset["rewards"], dataset["terminals"]):
        ep_ret += float(r)
        ep_len += 1
        if d or ep_len == max_episode_steps:
            returns.append(ep_ret)
            lengths.append(ep_len)
            ep_ret, ep_len = 0.0, 0
    lengths.append(ep_len)  # but still keep track of number of steps
    assert sum(lengths) == len(dataset["rewards"])
    return min(returns), max(returns)


def modify_reward(dataset, env_name, max_episode_steps=1000):
    if any(s in env_name for s in ("halfcheetah", "hopper", "walker2d")):
        min_ret, max_ret = return_reward_range(dataset, max_episode_steps)
        dataset["rewards"] /= max_ret - min_ret
        dataset["rewards"] *= max_episode_steps
    elif "antmaze" in env_name:
        dataset["rewards"] -= 1.0

def corrupt_dynamics_func(d4rl_dataset, load_path, state_dim, action_dim, config):
    random_num = np.random.random(d4rl_dataset["rewards"].shape)
    indexs = np.where(random_num < config.corruption_rate)
    # save original next obs
    original_next_obs = d4rl_dataset["next_observations"][indexs].copy()
    obs_std = d4rl_dataset["next_observations"].std(axis=0)

    # adversarial attack dynamics
    observation = torch.from_numpy(original_next_obs.copy()).to(config.device)
    obs_std_torch = torch.from_numpy(obs_std.reshape(1, state_dim)).to(config.device)
    M = observation.shape[0]
    update_times, step_size = 10, 0.1
    actor_tmp = Actor(state_dim, action_dim, config.hidden_dim, config.max_action)
    actor_tmp.to(config.device)
    critic_tmp = VectorizedCritic(state_dim, action_dim, config.hidden_dim, config.num_critics)
    critic_tmp.to(config.device)
    state_dict = torch.load(load_path)
    actor_tmp.load_state_dict(state_dict["actor"])
    critic_tmp.load_state_dict(state_dict["critic"])

    def sample_random(size):
        return 2 * config.corruption_range * obs_std_torch * (torch.rand(size, state_dim, device=config.device) - 0.5) 

    def optimize_para(para, observation, loss_fun, update_times, step_size, eps, std):
        for i in range(update_times):
            para = torch.nn.Parameter(para.clone(), requires_grad=True)
            optimizer = torch.optim.Adam([para], lr=step_size * eps) 
            loss = loss_fun(observation, para)
            # optimize noised obs
            optimizer.zero_grad()
            loss.mean().backward()
            optimizer.step()
            para = torch.maximum(torch.minimum(para, eps * std), -eps * std).detach()
        return para 

    def _loss_Q(observation, para):
        noised_obs = observation + para
        pred_actions = actor_tmp(noised_obs,  deterministic=True)[0]
        return critic_tmp(observation, pred_actions) 

    split = 10
    attack_obs = np.zeros((M, state_dim))
    pointer = 0
    for i in range(split):
        number = M // split if i < split -1 else M - pointer
        temp_obs = observation[pointer:pointer + number]
        para = sample_random(number).reshape(-1, state_dim)
        para = optimize_para(para, temp_obs, _loss_Q, update_times, step_size, config.corruption_range, obs_std_torch)
        noise_obs_final = para.detach()
        attack_obs[pointer:pointer + number] = noise_obs_final.cpu().numpy().reshape(-1, state_dim) + temp_obs.cpu().numpy().reshape(-1, state_dim)
        pointer += number
    
    # clear gpu cache
    critic_tmp.to('cpu')
    actor_tmp.to('cpu')
    torch.cuda.empty_cache()
    ### save data
    save_dict = {}
    save_dict['index'] = indexs
    save_dict['next_observations'] = attack_obs 
    env_dir = config.env_name.split('-')[0]
    path = os.path.join('./log_attack_data/{}/'.format(env_dir), "attack_data_corrupt{}_rate{}.pt".format(config.corruption_range, config.corruption_rate))
    if not os.path.exists('./log_attack_data/{}/'.format(env_dir)):
        os.makedirs(path)
    torch.save(save_dict,path)
    


# @pyrallis.wrap()
def train(config: TrainConfig):
    set_seed(config.train_seed, deterministic_torch=config.deterministic_torch)
    wandb_init(asdict(config))

    # data, evaluation, env setup
    eval_env = wrap_env(gym.make(config.env_name))
    state_dim = eval_env.observation_space.shape[0]
    action_dim = eval_env.action_space.shape[0]

    d4rl_dataset = d4rl.qlearning_dataset(eval_env)

    if config.normalize_reward:
        modify_reward(d4rl_dataset, config.env_name)

    if (config.corruption_reward or config.corruption_dynamics):
        if config.random_corruption:
            print('random corruption')
            random_num = np.random.random(d4rl_dataset["rewards"].shape)
            indexs = np.where(random_num < config.corruption_rate)
            if config.corruption_dynamics: # corrupt dynamics
                print('attack dynamics')
                std = d4rl_dataset["next_observations"].std(axis=0).reshape(1,state_dim)
                d4rl_dataset["next_observations"][indexs] += \
                            np.random.uniform(-config.corruption_range, config.corruption_range, size=(indexs[0].shape[0], state_dim)) * std
            
            if config.corruption_reward: # corrupt rewards
                print('attack reward')
                d4rl_dataset["rewards"][indexs] = \
                            np.random.uniform(-config.corruption_range, config.corruption_range, size=indexs[0].shape[0])

        else:
            print('adversarial corruption')
            if config.corruption_reward:
                print('attack reward')
                random_num = np.random.random(d4rl_dataset["rewards"].shape)
                indexs = np.where(random_num < config.corruption_rate)
                # corrupt rewards
                d4rl_dataset["rewards"][indexs] *= - config.corruption_range
            
            if config.corruption_dynamics:
                print('attack dynamics')
                env_dir = config.env_name.split('-')[0]
                if config.gradient_attack:
                    corrupt_dynamics_func(d4rl_dataset, config.corrupt_model_path, state_dim, action_dim, config)
                    import pdb;pdb.set_trace()
                    # you can stop here and check the saved attack data
                print('loading path {}'.format(os.path.join('./log_attack_data/{}/'.format(env_dir), "attack_data_corrupt{}_rate{}.pt".format(config.corruption_range, config.corruption_rate))))
                data_dict = torch.load(os.path.join('./log_attack_data/{}/'.format(env_dir), "attack_data_corrupt{}_rate{}.pt".format(config.corruption_range, config.corruption_rate)))
                attack_indexs, next_observations  = data_dict['index'], data_dict['next_observations']
                d4rl_dataset["next_observations"][attack_indexs] = next_observations



    buffer = ReplayBuffer(
        state_dim=state_dim,
        action_dim=action_dim,
        buffer_size=config.buffer_size,
        device=config.device,
    )
    buffer.load_d4rl_dataset(d4rl_dataset)

    # Actor & Critic setup
    actor = Actor(state_dim, action_dim, config.hidden_dim, config.max_action)
    actor.to(config.device)
    actor_optimizer = torch.optim.Adam(actor.parameters(), lr=config.actor_learning_rate)
    critic = VectorizedCritic(
        state_dim, action_dim, config.hidden_dim, config.num_critics
    )
    critic.to(config.device)
    critic_optimizer = torch.optim.Adam(
        critic.parameters(), lr=config.critic_learning_rate
    )

    trainer = SACN(
        actor=actor,
        actor_optimizer=actor_optimizer,
        critic=critic,
        critic_optimizer=critic_optimizer,
        gamma=config.gamma,
        tau=config.tau,
        alpha_learning_rate=config.alpha_learning_rate,
        LCB_ratio=config.LCB_ratio,
        use_UW=config.use_UW,
        uncertainty_ratio=config.uncertainty_ratio,
        uncertainty_basic=config.uncertainty_basic,
        uncertainty_min=config.uncertainty_min,
        uncertainty_max=config.uncertainty_max,
        device=config.device,
    )
    # saving config to the checkpoint
    if config.checkpoints_path is not None:
        print(f"Checkpoints path: {config.checkpoints_path}")
        os.makedirs(config.checkpoints_path, exist_ok=True)
        with open(os.path.join(config.checkpoints_path, "config.yaml"), "w") as f:
            pyrallis.dump(config, f)

    total_updates = 0.0
    for epoch in trange(config.num_epochs, desc="Training"):
        # training
        for _ in trange(config.num_updates_on_epoch, desc="Epoch", leave=False):
            batch = buffer.sample(config.batch_size)
            update_info = trainer.update(batch)

            if total_updates % config.log_every == 0:
                if trainer.use_UW:
                    update_info['average uncertainty'] = np.mean(trainer.uncertainty.detach().cpu().numpy())
                wandb.log({"epoch": epoch, **update_info})

            total_updates += 1


        # evaluation
        if epoch % config.eval_every == 0 or epoch == config.num_epochs - 1:
            eval_returns = eval_actor(
                env=eval_env,
                actor=actor,
                n_episodes=config.eval_episodes,
                seed=config.eval_seed,
                device=config.device,
            )
            eval_log = {
                "eval/reward_mean": np.mean(eval_returns),
                "eval/reward_std": np.std(eval_returns),
                "epoch": epoch,
            }
            if hasattr(eval_env, "get_normalized_score"):
                normalized_score = eval_env.get_normalized_score(eval_returns) * 100.0
                eval_log["eval/normalized_score_mean"] = np.mean(normalized_score)
                eval_log["eval/normalized_score_std"] = np.std(normalized_score)

            wandb.log(eval_log)

            if ((epoch+1) % 1000 == 0) and config.checkpoints_path is not None:
                torch.save(
                    trainer.state_dict(),
                    os.path.join(config.checkpoints_path, f"{epoch}.pt"),
                )

    wandb.finish()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--env_name', type=str, default='halfcheetah-medium-v2')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--use_default_parameters', action='store_true', default=False)
    parser.add_argument('--corruption_reward', action='store_true', default=False)
    parser.add_argument('--corruption_dynamics', action='store_true', default=False)
    parser.add_argument('--random_corruption', action='store_true', default=False)
    parser.add_argument('--use_UW', action='store_true', default=False)
    parser.add_argument('--uncertainty_ratio', type=float, default=0.3)
    parser.add_argument('--corruption_range', type=float, default=0.3)
    parser.add_argument('--corruption_rate', type=float, default=0.1)
    args = parser.parse_args()
    print(args)

    ### modify config
    TrainConfig.env_name = args.env_name
    TrainConfig.train_seed = args.seed
    TrainConfig.corruption_reward = args.corruption_reward
    TrainConfig.corruption_dynamics = args.corruption_dynamics
    TrainConfig.random_corruption = args.random_corruption
    TrainConfig.corruption_range = args.corruption_range
    TrainConfig.corruption_rate = args.corruption_rate
    TrainConfig.use_UW = args.use_UW
    if args.use_default_parameters:
        get_attack_config(TrainConfig)
        get_UWMSG_config(TrainConfig)


    ## modify config
    group_name_center = 'reward' if TrainConfig.corruption_reward else 'dynamics'
    group_name_center = 'random_' + group_name_center if TrainConfig.random_corruption else 'adversarial_' + group_name_center
    TrainConfig.group = TrainConfig.group + '-{}'.format(group_name_center) 
    TrainConfig.group = TrainConfig.group if TrainConfig.env_name == 'halfcheetah-medium-v2' else TrainConfig.group + '_{}'.format(TrainConfig.env_name.split('-')[0])
    
    if args.use_UW:
        name = "UWMSG_corrupt{}_{}_QLCB{}_UW{}_seed{}".format(TrainConfig.corruption_range, TrainConfig.corruption_rate,  TrainConfig.LCB_ratio, TrainConfig.uncertainty_ratio, TrainConfig.train_seed)
    else:
        name = "MSG_corrupt{}_{}_QLCB{}_seed{}".format(TrainConfig.corruption_range, TrainConfig.corruption_rate, TrainConfig.LCB_ratio, TrainConfig.train_seed)
    
    TrainConfig.name = f"{name}-{TrainConfig.env_name}-{str(uuid.uuid4())[:8]}"
    if TrainConfig.checkpoints_path is not None:
        TrainConfig.checkpoints_path = os.path.join(TrainConfig.checkpoints_path, TrainConfig.name)
    
    train(TrainConfig)


