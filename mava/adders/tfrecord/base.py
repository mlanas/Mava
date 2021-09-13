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

"""Adders that use TFRecords to save experience to disk."""
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

import dm_env
from acme import types

from mava.specs import MAEnvironmentSpec

DEFAULT_SUBDIR = "~/tfrecords"


class TFRecordParallelAdder:
    """Base tfrecord adder class."""

    def __init__(
        self,
        environment_spec: MAEnvironmentSpec,
        subdir: str = DEFAULT_SUBDIR,
    ):
        """A TFRecord Base Adder.

        Args:
            environment_spec: specification of the environment. Used to
                determin dtypes for the tensors.
            transitions_per_file: number of transitions to store in each file.
            id: a string identifying this set of records.
            subdir: directory to which the records should be stored. Defualts to
                "~/mava/tfrecords/".

        """
        # Store env spec.
        self._environment_spec = environment_spec

        # Join id and subdir.
        self._subdir: str = subdir

        # Make the directory if it does not exist.
        Path(self._subdir).mkdir(parents=True, exist_ok=True)

    def _write(self) -> None:
        raise NotImplementedError

    def add_first(
        self, timestep: dm_env.TimeStep, extras: Dict[str, types.NestedArray] = {}
    ) -> None:
        """Record the first observation of a trajectory.

        Args:
            timestep: dict of agents first observation.
            extras: dict of optional extras

        """
        raise NotImplementedError

    def add(
        self,
        actions: Dict[str, types.NestedArray],
        next_timestep: dm_env.TimeStep,
        next_extras: Dict[str, types.NestedArray] = {},
    ) -> None:
        """Record an action and the following timestep.

        Args:
            actions: dict of agent actions.
            next_timestep: dict of agent observations.
            next_extras: dict of optional extras.

        """
        raise NotImplementedError
