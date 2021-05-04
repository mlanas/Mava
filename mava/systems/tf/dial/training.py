# python3
# Copyright 2021 InstaDeep Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# TODO (Kevin): implement DIAL trainer
# Helper resources
#   - single agent dqn learner in acme:
#           https://github.com/deepmind/acme/blob/master/acme/agents/tf/dqn/learning.py
#   - multi-agent ddpg trainer in mava: mava/systems/tf/maddpg/trainer.py
#   - https://github.com/deepmind/acme/agents/tf/r2d2/learning.py

"""DIAL trainer implementation."""
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import reverb
import sonnet as snt
import tensorflow as tf
import tree
from acme.tf import savers as tf2_savers
from acme.tf import utils as tf2_utils
from acme.utils import counting, loggers

import mava


class DIALTrainer(mava.Trainer):
    """DIAL trainer.
    This is the trainer component of a DIAL system. IE it takes a dataset as input
    and implements update functionality to learn from this dataset.
    """

    def __init__(
        self,
        agents: List[str],
        agent_types: List[str],
        networks: Dict[str, snt.Module],
        target_network: Dict[str, snt.Module],
        discount: float,
        huber_loss_parameter: float,
        target_update_period: int,
        dataset: tf.data.Dataset,
        shared_weights: bool = True,
        importance_sampling_exponent: float = None,
        policy_optimizer: snt.Optimizer = None,
        replay_client: Optional[reverb.Client] = None,
        clipping: bool = True,
        counter: counting.Counter = None,
        logger: loggers.Logger = None,
        checkpoint: bool = True,
        checkpoint_subpath: str = "Checkpoints",
        max_gradient_norm: Optional[float] = None,
    ):
        """Initializes the learner.
        Args:
          policy_network: the online (optimized) policy.
          target_policy_network: the target policy (which lags behind the online
            policy).
          discount: discount to use for TD updates.
          target_update_period: number of learner steps to perform before updating
            the target networks.
          dataset: dataset to learn from, whether fixed or from a replay buffer
            (see `acme.datasets.reverb.make_dataset` documentation).
          observation_network: an optional online network to process observations
            before the policy
          target_observation_network: the target observation network.
          policy_optimizer: the optimizer to be applied to the (policy) loss.
          clipping: whether to clip gradients by global norm.
          counter: counter object used to keep track of steps.
          logger: logger object to be used by learner.
          checkpoint: boolean indicating whether to checkpoint the learner.
        """

        self._agents = agents
        self._agent_types = agent_types
        self._shared_weights = shared_weights

        # Store online and target networks.
        self._policy_networks = networks
        self._target_policy_networks = target_network

        # self._observation_networks = observation_networks
        # self._target_observation_networks = target_observation_networks

        # General learner book-keeping and loggers.
        self._counter = counter or counting.Counter()
        self._logger = logger or loggers.make_default_logger("trainer")

        # Other learner parameters.
        self._discount = discount
        self._clipping = clipping

        # Necessary to track when to update target networks.
        self._num_steps = tf.Variable(0, dtype=tf.int32)
        self._target_update_period = target_update_period

        # Create an iterator to go through the dataset.
        # TODO(b/155086959): Fix type stubs and remove.
        self._iterator = iter(dataset)  # pytype: disable=wrong-arg-types

        # Create optimizers if they aren't given.
        self._policy_optimizer = policy_optimizer or snt.optimizers.Adam(1e-4)

        # Dictionary with network keys for each agent.
        self.agent_net_keys = {agent: agent for agent in self._agents}
        if self._shared_weights:
            self.agent_net_keys = {agent: agent.split("_")[0] for agent in self._agents}

        self.unique_net_keys = self._agent_types if shared_weights else self._agents

        self._system_checkpointer = {}
        if checkpoint:
            # TODO (dries): Address this new warning: WARNING:tensorflow:11 out
            #  of the last 11 calls to
            #  <function MultiDeviceSaver.save.<locals>.tf_function_save at
            #  0x7eff3c13dd30> triggered tf.function retracing. Tracing is
            #  expensive and the excessive number tracings could be due to (1)
            #  creating @tf.function repeatedly in a loop, (2) passing tensors
            #  with different shapes, (3) passing Python objects instead of tensors.
            for agent_key in self.unique_net_keys:
                objects_to_save = {
                    "counter": self._counter,
                    "policy": self._policy_networks[agent_key],
                    "observation": self._observation_networks[agent_key],
                    "target_policy": self._target_policy_networks[agent_key],
                    "policy_optimizer": self._policy_optimizer,
                    "num_steps": self._num_steps,
                }

                checkpointer_dir = os.path.join(checkpoint_subpath, agent_key)
                checkpointer = tf2_savers.Checkpointer(
                    time_delta_minutes=1,
                    add_uid=False,
                    directory=checkpointer_dir,
                    objects_to_save=objects_to_save,
                    enable_checkpointing=True,
                )
                self._system_checkpointer[agent_key] = checkpointer

        self._timestamp = None

    @tf.function
    def _update_target_networks(self) -> None:
        for key in self.unique_net_keys:
            # Update target network.
            online_variables = (
                # *self._observation_networks[key].variables,
                *self._policy_networks[key].variables,
            )
            target_variables = (
                # *self._target_observation_networks[key].variables,
                *self._target_policy_networks[key].variables,
            )

            # Make online -> target network update ops.
            if tf.math.mod(self._num_steps, self._target_update_period) == 0:
                for src, dest in zip(online_variables, target_variables):
                    dest.assign(src)
            self._num_steps.assign_add(1)

    @tf.function
    def _policy_actions_messages(
        self,
        target_obs_trans: Dict[str, np.ndarray],
        target_core_state: Dict[str, np.ndarray],
        target_core_message: Dict[str, np.ndarray],
    ) -> Any:
        actions = {}
        messages = {}

        for agent in self._agents:
            time.time()
            agent_key = self.agent_net_keys[agent]
            target_trans_obs = target_obs_trans[agent]
            # TODO (dries): Why is there an extra tuple
            #  wrapping that needs to be removed?
            agent_core_state = target_core_state[agent][0]
            agent_core_message = target_core_message[agent][0]

            transposed_obs = tf2_utils.batch_to_sequence(target_trans_obs)

            (output_actions, output_messages), _ = snt.static_unroll(
                self._target_policy_networks[agent_key],
                transposed_obs,
                agent_core_state,
                agent_core_message,
            )
            actions[agent] = tf2_utils.batch_to_sequence(output_actions)
            messages[agent] = tf2_utils.batch_to_sequence(output_messages)
        return actions, messages

    def _step(self) -> Dict[str, Dict[str, Any]]:
        # TODO Kevin: Implement DIAL trainer algorithm

        # Update the target networks
        self._update_target_networks()

        inputs = next(self._iterator)

        data = tree.map_structure(
            lambda v: tf.expand_dims(v, axis=0) if len(v.shape) <= 1 else v, inputs.data
        )
        data = tf2_utils.batch_to_sequence(data)

        # print(data)
        # raise AssertionError

        observations, actions, rewards, discounts, done, extra = data

        # print('core')
        # print(extra["core_states"])
        # core_states = tree.map_structure(lambda x: x[0], extra["core_states"])
        core_states = extra["core_states"]
        # print(core_states)

        # Need to loop backwards through time
        # for t=T to 1, -1 do

        bs = actions["agent_0"].shape[1]
        T = actions["agent_0"].shape[0]

        logged_losses: Dict[str, Dict[str, Any]] = {}
        agent_type = self._agent_types[0]

        with tf.GradientTape(persistent=True) as tape:
            total_loss = {}

            # for each batch
            for b in range(bs):
                total_loss[b] = tf.zeros(1)
                # For t=T to 1, -1 do
                for t in range(T - 1, 0, -1):  # Should it be (T,1,-1)?

                    # For each agent a do
                    for agent_id in observations.keys():
                        # All at timestep t
                        agent_input = observations[agent_id].observation[:, b]
                        # (sequence,batch,1)
                        message = core_states[agent_id]["message"][:, b]
                        # (sequence,batch,)
                        state = core_states[agent_id]["state"][:, b]
                        # (sequence,batch,128)
                        action = actions[agent_id][:, b]
                        # (sequence,batch,1)
                        reward = rewards[agent_id][:, b]
                        # (sequence,batch,1)
                        # discount = discounts[agent_id][t, b]
                        discount = tf.cast(
                            self._discount, dtype=discounts[agent_id][t, b].dtype
                        )
                        # (sequence,batch,1)
                        terminal = done[t, b]

                        # y_t_a = r_t
                        y_action = reward[t]
                        y_message = reward[t]

                        # y_t_a = r_t + discount * max_u Q(t)
                        if not terminal:
                            batched_observation = tf2_utils.add_batch_dim(
                                agent_input[t]
                            )
                            batched_state = tf2_utils.add_batch_dim(state[t])
                            batched_message = tf2_utils.add_batch_dim(message[t])

                            (q_t, m_t), s = self._target_policy_networks[agent_type](
                                batched_observation, batched_state, batched_message
                            )
                            y_action += discount * q_t[0][action[t]]
                            y_message += discount * m_t[tf.argmax(m_t)[0]]

                        # d_Q_t_a = y_t_a - Q(t-1)
                        batched_observation = tf2_utils.add_batch_dim(
                            agent_input[t - 1]
                        )
                        batched_state = tf2_utils.add_batch_dim(state[t - 1])
                        batched_message = tf2_utils.add_batch_dim(message[t - 1])

                        (q_t1, m_t1), s = self._policy_networks[agent_type](
                            batched_observation, batched_state, batched_message
                        )

                        td_action = y_action - q_t1[0][action[t - 1]]

                        # d_theta = d_theta + d_Q_t_a ^ 2
                        total_loss[b] += td_action ** 2

                        # Communication grads
                        td_comm = y_message - m_t1[tf.argmax(m_t1)[0]]

                        total_loss[b] += td_action ** 2 + td_comm ** 2

        for b in range(bs):
            policy_variables = self._policy_networks[agent_type].trainable_variables
            policy_gradients = tape.gradient(total_loss[b], policy_variables)
            if self._clipping:
                policy_gradients = tf.clip_by_global_norm(policy_gradients, 40.0)[0]

            # Apply gradients.
            self._policy_optimizer.apply(policy_gradients, policy_variables)

            logged_losses.update(
                {
                    agent_type: {
                        "policy_loss": total_loss[b],
                    }
                }
            )

        # print(total_loss)
        # raise AssertionError

        return logged_losses

    def step(self) -> None:
        # Run the learning step.
        fetches = self._step()

        # Compute elapsed time.
        timestamp = time.time()
        if self._timestamp:
            elapsed_time = timestamp - self._timestamp
        else:
            elapsed_time = 0
        self._timestamp = timestamp  # type: ignore

        # Update our counts and record it.
        counts = self._counter.increment(steps=1, walltime=elapsed_time)
        fetches.update(counts)

        # Checkpoint the networks.
        if len(self._system_checkpointer.keys()) > 0:
            for agent_key in self.unique_net_keys:
                checkpointer = self._system_checkpointer[agent_key]
                checkpointer.save()

        self._logger.write(fetches)

    def get_variables(self, names: Sequence[str]) -> Dict[str, Dict[str, np.ndarray]]:
        variables: Dict[str, Dict[str, np.ndarray]] = {}
        for network_type in names:
            variables[network_type] = {}
            for agent in self.unique_net_keys:
                variables[network_type][agent] = tf2_utils.to_numpy(
                    self._system_network_variables[network_type][agent]
                )
        return variables
