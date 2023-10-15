def get_attack_config(config):
    if config.env_name.startswith('halfcheetah'):
        if config.random_corruption:
            if config.corruption_reward:
                config.corruption_range = 30.0
                config.corruption_rate = 0.2
            elif config.corruption_dynamics:
                config.corruption_range = 2.0
                config.corruption_rate = 0.2
        else:
            if config.corruption_reward:
                config.corruption_range = 3.0
                config.corruption_rate = 0.2
            elif config.corruption_dynamics:
                config.corruption_range = 1.2
                config.corruption_rate = 0.3
    elif config.env_name.startswith('walker2d'):
        if config.random_corruption:
            if config.corruption_reward:
                config.corruption_range = 30.0
                config.corruption_rate = 0.3
            elif config.corruption_dynamics:
                config.corruption_range = 0.5
                config.corruption_rate = 0.1
        else:
            if config.corruption_reward:
                config.corruption_range = 3.0
                config.corruption_rate = 0.2
            elif config.corruption_dynamics:
                config.corruption_range = 0.3
                config.corruption_rate = 0.1
    elif config.env_name.startswith('hopper'):
        if config.random_corruption:
            if config.corruption_reward:
                config.corruption_range = 30.0
                config.corruption_rate = 0.2
            elif config.corruption_dynamics:
                config.corruption_range = 0.5
                config.corruption_rate = 0.1
        else:
            if config.corruption_reward:
                config.corruption_range = 5.0
                config.corruption_rate = 0.1
            elif config.corruption_dynamics:
                config.corruption_range = 0.5
                config.corruption_rate = 0.1
    else:
        raise NotImplementedError
    

def get_UWMSG_config(config):
    if config.env_name.startswith('halfcheetah'):
        if config.random_corruption:
            if config.corruption_reward:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.7
            elif config.corruption_dynamics:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.5
        else:
            if config.corruption_reward:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.7
            elif config.corruption_dynamics:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.2
    elif config.env_name.startswith('walker2d'):
        if config.random_corruption:
            if config.corruption_reward:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.3
            elif config.corruption_dynamics:
                config.LCB_ratio = 6.0
                config.uncertainty_ratio = 0.5
        else:
            if config.corruption_reward:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.5
            elif config.corruption_dynamics:
                config.LCB_ratio = 4.0
                config.uncertainty_ratio = 0.5
    elif config.env_name.startswith('hopper'):
        if config.random_corruption:
            if config.corruption_reward:
                config.LCB_ratio = 6.0
                config.uncertainty_ratio = 0.7
            elif config.corruption_dynamics:
                config.LCB_ratio = 6.0
                config.uncertainty_ratio = 0.7
        else:
            if config.corruption_reward:
                config.LCB_ratio = 6.0
                config.uncertainty_ratio = 0.7
            elif config.corruption_dynamics:
                config.LCB_ratio = 6.0
                config.uncertainty_ratio = 1.0
    else:
        raise NotImplementedError

        

