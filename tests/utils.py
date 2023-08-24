from typing import List

import ubiconfig
from attrs import define
from pubtools.pulplib import YumRepository, RpmDependency


def create_and_insert_repo(**kwargs):
    pulp = kwargs.pop("pulp")
    pulp.insert_repository(YumRepository(**kwargs))

    return pulp.client.get_repository(kwargs["id"])


class MockLoader:
    def load_all(self):
        config_raw_1 = {
            "modules": {
                "include": [
                    {
                        "name": "fake_name",
                        "stream": "fake_stream",
                    }
                ]
            },
            "packages": {
                "include": ["package-name-.*", "gcc.*", "httpd.src", "pkg-debuginfo.*"],
                "exclude": ["package-name*.*", "kernel", "kernel.x86_64"],
            },
            "content_sets": {
                "rpm": {"output": "cs_rpm_out", "input": "cs_rpm_in"},
                "srpm": {"output": "cs_srpm_out", "input": "cs_srpm_in"},
                "debuginfo": {"output": "cs_debug_out", "input": "cs_debug_in"},
            },
            "arches": ["x86_64", "src"],
        }

        config_raw_2 = {
            "modules": {
                "include": [
                    {
                        "name": "fake_name",
                        "stream": "fake_stream",
                    }
                ]
            },
            "packages": {
                "include": [
                    "package-name-.*",
                    "gcc.*",
                    "httpd.src",
                    "pkg-debuginfo.*",
                    "bind.*",
                ],
                "exclude": ["package-name*.*", "kernel", "kernel.x86_64"],
            },
            "content_sets": {
                "rpm": {"output": "cs_rpm_out", "input": "cs_rpm_in_other"},
                "srpm": {"output": "cs_srpm_out", "input": "cs_srpm_in_other"},
                "debuginfo": {"output": "cs_debug_out", "input": "cs_debug_in_other"},
            },
            "arches": ["x86_64", "src"],
        }
        return [
            ubiconfig.UbiConfig.load_from_dict(config, file, "8")
            for config, file in [(config_raw_1, "file_1"), (config_raw_2, "file_2")]
        ]


@define
class MockedRedis:
    data: dict

    def set(self, key: str, value: str, **kwargs) -> None:
        self.data[key] = value

    def get(self, key: str) -> str:
        return self.data.get(key)

    def keys(self) -> List[str]:
        return list(self.data.keys())


def rpmdeps_from_names(*names):
    return {RpmDependency(name=name) for name in names}
