import operator
import random

import numpy as np
import torch

import config


class SegmentTree(object):
    def __init__(self, capacity, operation, neutral_element):

        assert capacity > 0 and capacity & (capacity - 1) == 0, \
            "capacity must be positive and a power of 2."
        self._capacity = capacity
        self._value = [neutral_element for _ in range(2 * capacity)]
        self._operation = operation

    def _reduce_helper(self, start, end, node, node_start, node_end):
        if start == node_start and end == node_end:
            return self._value[node]
        mid = (node_start + node_end) // 2
        if end <= mid:
            return self._reduce_helper(start, end, 2 * node, node_start, mid)
        else:
            if mid + 1 <= start:
                return self._reduce_helper(start, end,
                                           2 * node + 1, mid + 1, node_end)
            else:
                return self._operation(
                    self._reduce_helper(start, mid,
                                        2 * node, node_start, mid),
                    self._reduce_helper(mid + 1, end,
                                        2 * node + 1, mid + 1, node_end)
                )

    def reduce(self, start=0, end=None):

        if end is None:
            end = self._capacity
        if end < 0:
            end += self._capacity
        end -= 1
        return self._reduce_helper(start, end, 1, 0, self._capacity - 1)

    def __setitem__(self, idx, val):
        # index of the leaf
        idx += self._capacity
        self._value[idx] = val
        idx //= 2
        while idx >= 1:
            self._value[idx] = self._operation(
                self._value[2 * idx],
                self._value[2 * idx + 1]
            )
            idx //= 2

    def __getitem__(self, idx):
        assert 0 <= idx < self._capacity
        return self._value[self._capacity + idx]


class SumSegmentTree(SegmentTree):
    def __init__(self, capacity):
        super(SumSegmentTree, self).__init__(
            capacity=capacity,
            operation=operator.add,
            neutral_element=0.0
        )

    def sum(self, start=0, end=None):
        """Returns arr[start] + ... + arr[end]"""
        return super(SumSegmentTree, self).reduce(start, end)

    def find_prefixsum_idx(self, prefixsum):
        """Find the highest index `i` in the array such that
            sum(arr[0] + arr[1] + ... + arr[i - i]) <= prefixsum

        if array values are probabilities, this function
        allows to sample indexes according to the discrete
        probability efficiently.

        Parameters
        ----------
        perfixsum: float
            upperbound on the sum of array prefix

        Returns
        -------
        idx: int
            highest index satisfying the prefixsum constraint
        """
        assert 0 <= prefixsum <= self.sum() + 1e-5
        idx = 1
        while idx < self._capacity:  # while non-leaf
            if self._value[2 * idx] > prefixsum:
                idx = 2 * idx
            else:
                prefixsum -= self._value[2 * idx]
                idx = 2 * idx + 1
        return idx - self._capacity


class MinSegmentTree(SegmentTree):
    def __init__(self, capacity):
        super(MinSegmentTree, self).__init__(
            capacity=capacity,
            operation=min,
            neutral_element=float('inf')
        )

    def min(self, start=0, end=None):
        """Returns min(arr[start], ...,  arr[end])"""

        return super(MinSegmentTree, self).reduce(start, end)


class ReplayBuffer(object):
    def __init__(self, size, device):
        """Create Replay buffer.

        Parameters
        ----------
        size: int
            Max number of transitions to store in the buffer. When the buffer
            overflows the old memories are dropped.
        """
        self._storage = []
        self._maxsize = size
        self._next_idx = 0
        self._device = device

    def __len__(self):
        return len(self._storage)

    def add(self, args):
        if self._next_idx >= len(self._storage):
            self._storage.append(args)
        else:
            self._storage[self._next_idx] = args
        self._next_idx = (self._next_idx + 1) % self._maxsize

    def _encode_sample(self, idxes):
        batch_obs, batch_action, batch_reward, batch_obs_, batch_done, batch_steps = [], [], [], [], [], []
        
        for i in idxes:
            obs, action, reward, obs_, done = self._storage[i]

            # n steps forward
            steps = 1
            sum_reward = reward
            for j in range(1,config.n_steps):
                next_idx = (i+j) % self._maxsize
                if next_idx != self._next_idx and not done:
                    _, _, reward, obs_, done = self._storage[next_idx]
                    sum_reward += reward * config.gamma ** j
                    steps += 1
                else:
                    break

            batch_obs.append(obs)
            batch_action.append(action)
            batch_reward.append(sum_reward)
            batch_obs_.append(obs_)
            batch_done.append(done)
            batch_steps.append(steps)

        res = (
            torch.FloatTensor(np.stack(batch_obs)).to(self._device),
            torch.LongTensor(batch_action).unsqueeze(1).to(self._device),
            torch.FloatTensor(batch_reward).unsqueeze(1).to(self._device),
            torch.FloatTensor(np.stack(batch_obs_)).to(self._device),
            torch.FloatTensor(batch_done).unsqueeze(1).to(self._device),
            torch.FloatTensor(batch_steps).unsqueeze(1).to(self._device),
        )
        return res

    def sample(self, batch_size):
        """Sample a batch of experiences."""
        indexes = range(len(self._storage))
        idxes = [random.choice(indexes) for _ in range(batch_size)]
        return self._encode_sample(idxes)


class PrioritizedReplayBuffer(ReplayBuffer):
    def __init__(self, size, device, alpha, beta):
        """Create Prioritized Replay buffer.

        Parameters
        ----------
        size: int
            Max number of transitions to store in the buffer. When the buffer
            overflows the old memories are dropped.
        alpha: float
            how much prioritization is used
            (0 - no prioritization, 1 - full prioritization)

        See Also
        --------
        ReplayBuffer.__init__
        """
        super(PrioritizedReplayBuffer, self).__init__(size, device)
        assert alpha >= 0
        self._alpha = alpha

        it_capacity = 1
        while it_capacity < size:
            it_capacity *= 2

        self._it_sum = SumSegmentTree(it_capacity)
        self._it_min = MinSegmentTree(it_capacity)
        self._max_priority = 1.0
        self.beta = beta

    def add(self, *args, **kwargs):
        """See ReplayBuffer.store_effect"""
        idx = self._next_idx
        super().add(*args, **kwargs)
        self._it_sum[idx] = self._max_priority ** self._alpha
        self._it_min[idx] = self._max_priority ** self._alpha

    def _sample_proportional(self, batch_size):
        res = []
        p_total = self._it_sum.sum(0, len(self._storage) - 1)
        every_range_len = p_total / batch_size
        for i in range(batch_size):
            mass = random.random() * every_range_len + i * every_range_len
            idx = self._it_sum.find_prefixsum_idx(mass)
            res.append(idx)
        return res

    def sample(self, batch_size):
        """Sample a batch of experiences"""
        idxes = self._sample_proportional(batch_size)

        it_sum = self._it_sum.sum()
        p_min = self._it_min.min() / it_sum
        max_weight = (p_min * len(self._storage)) ** (-self.beta)

        p_samples = np.asarray([self._it_sum[idx] for idx in idxes]) / it_sum
        weights = (p_samples * len(self._storage)) ** (-self.beta) / max_weight
        weights = torch.from_numpy(weights.astype('float32'))
        weights = weights.to(self._device).unsqueeze(1)
        encoded_sample = self._encode_sample(idxes)
        return encoded_sample + (weights, idxes)

    def update_priorities(self, idxes, priorities):
        """Update priorities of sampled transitions"""
        assert len(idxes) == len(priorities)
        for idx, priority in zip(idxes, priorities):
            assert priority > 0
            assert 0 <= idx < len(self._storage)
            self._it_sum[idx] = priority ** self._alpha
            self._it_min[idx] = priority ** self._alpha

            self._max_priority = max(self._max_priority, priority)
