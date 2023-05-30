# Uncertainty Weighted MSG for Offline RL with Data Corruption

This repo contains the official implemented UWMSG algorithm for the paper "Corruption-Robust Offline Reinforcement Learning with General Function Approximation".


## Getting started
First install the requirements:
```bash
pip install -r requirements/requirements_dev.txt
```

Run UWMSG with random reward corruption:
```bash
CUDA_VISIBLE_DEVICES=${gpu} python UWMSG.py --random_corruption  --corruption_reward --corruption_range ${corruption_range} --corruption_rate ${corruption_rate}  --env_name ${env_name} --seed ${seed} --use_UW 
```
${env_name} can be 'halfcheetah-medium-v2' and 'walker2d-medium-replay-v2'. ${corruption_range} and ${corruption_rate} are hyperparameters listed in our appendix. 

Run UWMSG with random dynamics corruption:
```bash
CUDA_VISIBLE_DEVICES=${gpu} python UWMSG.py --random_corruption  --corruption_dynamics --corruption_range ${corruption_range} --corruption_rate ${corruption_rate}  --env_name ${env_name} --seed ${seed} --use_UW 
```

Run UWMSG with adversarial reward corruption:
```bash
CUDA_VISIBLE_DEVICES=${gpu} python UWMSG.py --corruption_reward --corruption_range ${corruption_range} --corruption_rate ${corruption_rate}  --env_name ${env_name} --seed ${seed} --use_UW 
```

Run UWMSG with adversarial dynamics corruption:
```bash
CUDA_VISIBLE_DEVICES=${gpu} python UWMSG.py  --corruption_dynamics --corruption_range ${corruption_range} --corruption_rate ${corruption_rate}  --env_name ${env_name} --seed ${seed} --use_UW 
```

## Baselines
You can replace the UWMSG.py with SACN.py and EDAC.py to run SACN and EDAC. In addition, by removing the flag '--use_UW', you can run the MSG algorithm.



