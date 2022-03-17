import json
import logging
from typing import Dict, List

import redis
from pubtools.pulplib import RpmUnit

from ubi_manifest.worker.tasks.celery import app
from ubi_manifest.worker.tasks.depsolver.models import DepsolverItem, UbiUnit
from ubi_manifest.worker.tasks.depsolver.rpm_depsolver import Depsolver
from ubi_manifest.worker.tasks.depsolver.ubi_config import UbiConfigLoader
from ubi_manifest.worker.tasks.depsolver.utils import (
    make_pulp_client,
    parse_blacklist_config,
    remap_keys,
    split_filename,
)

_LOG = logging.getLogger(__name__)


@app.task
def depsolve_task(ubi_repo_ids: List[str]) -> None:
    """
    Run depsolvers for given ubi_repo_ids - it's expected that id of binary
    repositories are provided. Debuginfo and SRPM repos related to those ones
    provided as parameter are automatically resolved as well, because content
    of debuginfo and SRPM repos is dependent on the content of binary repo.
    Depsolved units are saved to redis - key is a destination repository id
    and value is a list of items, where item is a dict with keys:
    (source_repo_id, unit_type, unit_attr, value). Note that value in redis
    is stored as json string.
    """
    ubi_config_loader = UbiConfigLoader(app.conf["ubi_config_url"])

    with make_pulp_client(
        app.conf["pulp_url"],
        app.conf["pulp_username"],
        app.conf["pulp_password"],
        app.conf["pulp_insecure"],
    ) as client:
        repos_map = {}
        debuginfo_dep_map = {}
        dep_map = {}
        in_source_rpm_repos = []
        for ubi_repo_id in ubi_repo_ids:
            repo = client.get_repository(ubi_repo_id)
            debuginfo_repo = repo.get_debug_repository()
            srpm_repo = repo.get_source_repository()

            version = repo.ubi_config_version
            # get proper ubi_config for given content_set and version
            config = ubi_config_loader.get_config(repo.content_set, version)
            # config not found for specific version, try to fallback to default version
            if config is None:
                config = ubi_config_loader.get_config(
                    repo.content_set, version.split(".")[0]
                )
            # create rhel_repo:ubi_repo mapping
            for _repo, sources in zip(
                [repo, debuginfo_repo, srpm_repo],
                [
                    repo.population_sources,
                    debuginfo_repo.population_sources,
                    srpm_repo.population_sources,
                ],
            ):
                for item in sources:
                    repos_map[item] = _repo.id

            in_source_rpm_repos.extend(_get_population_sources(client, srpm_repo))
            whitelist, debuginfo_whitelist = _filter_whitelist(config)
            blacklist = parse_blacklist_config(config)

            dep_map[repo.id] = _make_depsolver_item(client, repo, whitelist, blacklist)
            debuginfo_dep_map[debuginfo_repo.id] = _make_depsolver_item(
                client, debuginfo_repo, debuginfo_whitelist, blacklist
            )
        # run depsolver for binary repos
        _LOG.info("Running depsolver for RPM repos: %s", list(dep_map.keys()))
        # TODO this blocks task from processing, depsolving of debuginfo packages
        # could be moved to the Depsolver. It should lead to more async processing
        # and better performance
        out = _run_depsolver(list(dep_map.values()), repos_map, in_source_rpm_repos)

        # generate missing debuginfo packages
        # TODO this seems to generate too many debuginfo packages - fix after tests with real data
        for ubi_repo_id, pkg_list in out.items():
            debuginfo_to_add = set()
            for pkg in pkg_list:
                # inspired with pungi depsolver
                if pkg.sourcerpm:
                    source_name = split_filename(pkg.sourcerpm)[0]
                    debuginfo_to_add.add(f"{pkg.name}-debuginfo")
                    debuginfo_to_add.add(f"{source_name}-debugsource")

            _id = client.get_repository(ubi_repo_id).get_debug_repository().id
            # update whitelist for given ubi depsolver item
            debuginfo_dep_map[_id].whitelist.update(debuginfo_to_add)

        # run depsolver for debuginfo repo
        _LOG.info(
            "Running depsolver for DEBUGINFO repos: %s", list(debuginfo_dep_map.keys())
        )
        debuginfo_out = _run_depsolver(
            list(debuginfo_dep_map.values()), repos_map, in_source_rpm_repos
        )

    out.update(debuginfo_out)
    # save depsolved data to redis
    _save(out)


def _save(data: Dict[str, List[UbiUnit]]) -> None:
    redis_client = redis.from_url(app.conf.backend)

    data_for_redis = {}
    for repo_id, units in data.items():
        for unit in units:
            if unit.isinstance_inner_unit(RpmUnit):
                item = {
                    "src_repo_id": unit.associate_source_repo_id,
                    "unit_type": "RpmUnit",
                    "unit_attr": "filename",
                    "value": unit.filename,
                }

            data_for_redis.setdefault(repo_id, []).append(item)
    # save data to redis as key:json_string
    for key, values in data_for_redis.items():
        redis_client.set(
            f"manifest:{key}",
            json.dumps(values),
            ex=app.conf["ubi_manifest_data_expiration"],
        )


def _filter_whitelist(ubi_config):
    whitelist = set()
    debuginfo_whitelist = set()

    for pkg in ubi_config.packages.whitelist:
        if pkg.arch == "src":
            continue
        if pkg.name.endswith("debuginfo") or pkg.name.endswith("debugsource"):
            debuginfo_whitelist.add(pkg.name)
        else:
            whitelist.add(pkg.name)

    return whitelist, debuginfo_whitelist


def _make_depsolver_item(client, repo, whitelist, blacklist):
    in_pulp_repos = _get_population_sources(client, repo)
    return DepsolverItem(whitelist, blacklist, in_pulp_repos)


def _get_population_sources(client, repo):
    return [client.get_repository(repo_id) for repo_id in repo.population_sources]


def _run_depsolver(depolver_items, repos_map, in_source_rpm_repos):
    with Depsolver(depolver_items, in_source_rpm_repos) as depsolver:
        depsolver.run()
        exported = depsolver.export()
        out = remap_keys(repos_map, exported)
    return out
