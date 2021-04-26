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

import importlib
import typing
from typing import Dict, Tuple, Union

import acme
import dm_env
import numpy as np
import pytest
from pettingzoo.utils.env import AECEnv, ParallelEnv

from mava import specs as mava_specs
from mava.environment_loop import ParallelEnvironmentLoop, SequentialEnvironmentLoop
from mava.utils.wrapper_utils import convert_np_type
from mava.wrappers.pettingzoo import (
    PettingZooAECEnvWrapper,
    PettingZooParallelEnvWrapper,
)
from tests.enums import EnvSource, EnvSpec, EnvType, MockedEnvironments
from tests.mocks import (
    ParallelMAContinuousEnvironment,
    ParallelMADiscreteEnvironment,
    SequentialMAContinuousEnvironment,
    SequentialMADiscreteEnvironment,
)

"""
Helpers contains re-usable test functions.
"""

# TODO(Kale-ab): Better structure helper funcs


class Helpers:
    # Check all props are not none
    @staticmethod
    def verify_all_props_not_none(props_which_should_not_be_none: list) -> bool:
        return all(prop is not None for prop in props_which_should_not_be_none)

    # Return an env - currently Pettingzoo envs.
    @staticmethod
    def get_env(env_spec: EnvSpec) -> Tuple[Union[AECEnv, ParallelEnv], int]:
        env, num_agents = None, None
        if env_spec.env_source == EnvSource.PettingZoo:
            mod = importlib.import_module(env_spec.env_name)
            if env_spec.env_type == EnvType.Parallel:
                env = mod.parallel_env()  # type: ignore
            elif env_spec.env_type == EnvType.Sequential:
                env = mod.env()  # type: ignore
        else:
            raise Exception("Env_spec is not valid.")
        env.reset()  # type: ignore
        num_agents = len(env.agents)  # type: ignore
        return env, num_agents

    # Returns a wrapper function.
    @staticmethod
    def get_wrapper_function(
        env_spec: EnvSpec,
    ) -> dm_env.Environment:
        wrapper = None
        if env_spec.env_source == EnvSource.PettingZoo:
            if env_spec.env_type == EnvType.Parallel:
                wrapper = PettingZooParallelEnvWrapper
            elif env_spec.env_type == EnvType.Sequential:
                wrapper = PettingZooAECEnvWrapper
        else:
            raise Exception("Env_spec is not valid.")
        return wrapper

    # Returns an env loop.
    @staticmethod
    def get_env_loop(
        env_spec: EnvSpec,
    ) -> acme.core.Worker:
        env_loop = None
        if env_spec.env_type == EnvType.Parallel:
            env_loop = ParallelEnvironmentLoop
        elif env_spec.env_type == EnvType.Sequential:
            env_loop = SequentialEnvironmentLoop
        else:
            raise Exception("Env_spec is not valid.")
        return env_loop

    """Function that retrieves a mocked env, based on
    env_spec."""

    @staticmethod
    def get_mocked_env(
        env_spec: EnvSpec,
    ) -> Union[
        ParallelMADiscreteEnvironment,
        SequentialMADiscreteEnvironment,
        ParallelMAContinuousEnvironment,
        SequentialMAContinuousEnvironment,
    ]:
        env = None
        if not hasattr(env_spec, "env_name"):
            raise Exception("No env_name passed in.")

        if not hasattr(env_spec, "env_type"):
            raise Exception("No env_type passed in.")
        env_name = env_spec.env_name

        if env_name is MockedEnvironments.Mocked_Dicrete:
            if env_spec.env_type == EnvType.Parallel:
                env = ParallelMADiscreteEnvironment(
                    num_actions=18,
                    num_observations=2,
                    obs_shape=(84, 84, 4),
                    obs_dtype=np.float32,
                    episode_length=10,
                )
            elif env_spec.env_type == EnvType.Sequential:
                env = SequentialMADiscreteEnvironment(
                    num_actions=18,
                    num_observations=2,
                    obs_shape=(84, 84, 4),
                    obs_dtype=np.float32,
                    episode_length=10,
                )
        elif env_name is MockedEnvironments.Mocked_Continous:
            if env_spec.env_type == EnvType.Parallel:
                env = ParallelMAContinuousEnvironment(
                    action_dim=2,
                    observation_dim=2,
                    bounded=True,
                    episode_length=10,
                )
            elif env_spec.env_type == EnvType.Sequential:
                env = SequentialMAContinuousEnvironment(
                    action_dim=2,
                    observation_dim=2,
                    bounded=True,
                    episode_length=10,
                )

        if env is None:
            raise Exception("Env_spec is not valid.")
        return env

    @staticmethod
    def is_mocked_env(
        env_name: str,
    ) -> bool:
        mock = False
        if (
            env_name is MockedEnvironments.Mocked_Continous
            or env_name is MockedEnvironments.Mocked_Dicrete
        ):
            mock = True
        return mock

    # Returns a wrapped env and specs
    @staticmethod
    def get_wrapped_env(
        env_spec: EnvSpec,
    ) -> Tuple[dm_env.Environment, acme.specs.EnvironmentSpec]:

        specs = None
        if Helpers.is_mocked_env(env_spec.env_name):
            wrapped_env = Helpers.get_mocked_env(env_spec)
            specs = wrapped_env._specs
        else:
            env, num_agents = Helpers.get_env(env_spec)
            wrapper_func = Helpers.get_wrapper_function(env_spec)
            wrapped_env = wrapper_func(env)
            specs = Helpers.get_pz_env_spec(wrapped_env)._specs
        return wrapped_env, specs

    # Returns a petting zoo environment spec.
    @staticmethod
    def get_pz_env_spec(environment: dm_env.Environment) -> dm_env.Environment:
        return mava_specs.MAEnvironmentSpec(environment)

    # Seeds action space
    @staticmethod
    def seed_action_space(
        env_wrapper: Union[PettingZooAECEnvWrapper, PettingZooParallelEnvWrapper],
        random_seed: int,
    ) -> None:
        [
            env_wrapper.action_spaces[agent].seed(random_seed)
            for agent in env_wrapper.agents
        ]

    @staticmethod
    def compare_dicts(dictA: Dict, dictB: Dict) -> bool:
        typesA = [type(k) for k in dictA.values()]
        typesB = [type(k) for k in dictB.values()]

        return (dictA == dictB) and (typesA == typesB)

    @staticmethod
    def assert_valid_episode(episode_result: Dict) -> None:
        assert (
            episode_result["episode_length"] > 0
            and episode_result["mean_episode_return"] is not None
            and episode_result["steps_per_second"] is not None
            and episode_result["episodes"] == 1
            and episode_result["steps"] > 0
        )

    @staticmethod
    def assert_env_reset(
        wrapped_env: dm_env.Environment,
        dm_env_timestep: dm_env.TimeStep,
        env_spec: EnvSpec,
    ) -> None:
        if env_spec.env_type == EnvType.Parallel:
            rewards_spec = wrapped_env.reward_spec()
            expected_rewards = {
                agent: convert_np_type(rewards_spec[agent].dtype, 0)
                for agent in wrapped_env.agents
            }

            discount_spec = wrapped_env.discount_spec()
            expected_discounts = {
                agent: convert_np_type(rewards_spec[agent].dtype, 1)
                for agent in wrapped_env.agents
            }

            Helpers.compare_dicts(
                dm_env_timestep.reward,
                expected_rewards,
            ), "Failed to reset reward."
            Helpers.compare_dicts(
                dm_env_timestep.discount,
                expected_discounts,
            ), "Failed to reset discount."

        elif env_spec.env_type == EnvType.Sequential:
            for agent in wrapped_env.agents:
                rewards_spec = wrapped_env.reward_spec()
                expected_reward = convert_np_type(rewards_spec[agent].dtype, 0)

                discount_spec = wrapped_env.discount_spec()
                expected_discount = convert_np_type(discount_spec[agent].dtype, 1)

                assert dm_env_timestep.reward == expected_reward and type(
                    dm_env_timestep.reward
                ) == type(expected_reward), "Failed to reset reward."
                assert dm_env_timestep.discount == expected_discount and type(
                    dm_env_timestep.discount
                ) == type(expected_discount), "Failed to reset discount."

    @staticmethod
    def mock_done() -> bool:
        return True


@typing.no_type_check
@pytest.fixture
def helpers() -> Helpers:
    return Helpers
