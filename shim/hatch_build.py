# ruff: noqa: D101, D102

"""Hatchling custom metadata hook.

Temporary fix until Astral decides how to handle dynamic project versions
in the uv.lock file.

See https://github.com/astral-sh/uv/issues/7533

To enable, place this file next to and add the following line to the
pyproject.toml file:

    [tool.hatch.metadata.hooks.custom]

To override, set SETUPTOOLS_SCM_PRETEND_VERSION environment variable to the
desired version before building, or set CI=1 (already set in GitHub actions)
to let hatch-vcs (using setuptools-scm) determine the version from the git tag.
"""

import os

from hatchling.metadata.plugin.interface import MetadataHookInterface

if not os.environ.get("SETUPTOOLS_SCM_PRETEND_VERSION") and not os.environ.get("CI"):
    os.environ["SETUPTOOLS_SCM_PRETEND_VERSION"] = "0.dev0+local"

class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        pass
