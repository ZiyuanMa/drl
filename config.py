
# training setting
env_name = 'MsPacman-v0'
training_steps = 10000000


# deep q-learning

grad_norm=10
batch_size=32
double_q=True
buffer_size=60000
exploration_fraction=0.1
exploration_final_eps=0.01
train_freq=4
learning_starts=30000
save_interval=50000
target_network_update_freq=4000
gamma=0.99
prioritized_replay=True
prioritized_replay_alpha=0.6
prioritized_replay_beta0=0.4
dueling=True
atom_num=51
min_value=-10
max_value=10
n_steps = 3

test_epsilon = 0.05