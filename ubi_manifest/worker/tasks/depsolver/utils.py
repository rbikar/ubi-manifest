from pubtools.pulplib import Client, Criteria
import os
import logging
from rpm import labelCompare as label_compare
from itertools import chain
from collections import defaultdict, deque
from concurrent.futures import as_completed
from pubtools.pulplib import Criteria
from pubtools.pulplib import Matcher as PulpLibMatcher
from more_executors.futures import f_flat_map, f_return, f_sequence, f_proxy
from more_executors import Executors
from logging import getLogger

_LOG = getLogger(__name__)


def _create_or_criteria(fields, values):
    # fields - list/tuple of fields [field1, field2]
    # values - list of tuples [(field1 value, field2 value), ...]
    # creates criteria for pulp query in a following way
    # one tuple in values uses AND logic
    # each criteria for one tuple are agregated by to or_criteria list
    or_criteria = []

    for val_tuple in values:
        inner_and_criteria = []
        if len(val_tuple) != len(fields):
            raise ValueError
        for index, field in enumerate(fields):

            inner_and_criteria.append(Criteria.with_field(field, val_tuple[index]))

        or_criteria.append(Criteria.and_(*inner_and_criteria))

    return or_criteria


def flatten_list_of_sets(list_of_sets):
    out = set()
    for one_set in list_of_sets:
        out |= one_set

    return f_return(out)


from pubtools.pulplib import RpmUnit, ModulemdUnit


def get_n_latest_from_content(content, modular_rpms=None):
    name_rpms_maps = {}
    for c in content:
        if modular_rpms:
            if c.filename in modular_rpms:
                _LOG.debug(f"skipping modular {c.filename}")
                continue

        name_rpms_maps.setdefault(c.name, []).append(c)

    out = []
    for rpm_list in name_rpms_maps.values():
        rpm_list.sort(key=vercmp_sort())
        _keep_n_latest_rpms(rpm_list)
        out.extend(rpm_list)

    return out


import re


def parse_bool_deps(dep_clause) -> set:
    ###remove paranthesis and split to terms
    _dep = re.sub(r"\(|\)", "", dep_clause)
    to_parse = _dep.split()

    operators = set(
        [
            "if",
            "else",
            "and",
            "or",
            "unless",
            "with",
            "without",
        ]
    )

    operator_num = set(["<", "<=", "=", ">", ">="])
    skip_next = False
    pkg_names = set()
    # nested = 0
    for item in to_parse:
        ###poresit pak nested ---taky skiped
        # if item == "(":
        #    nested += 1

        # skip item imediately apearing after num operator
        if skip_next:
            skip_next = False
            continue
        # skip operator
        if item in operators:
            continue

        # after num operator there is usually evr, we want to skip that as well
        if item in operator_num:
            skip_next = True
            continue

        pkg_names.add(item)
    return pkg_names


def vercmp_sort():
    class Klass(object):
        def __init__(self, package):
            self.evr_tuple = (package.epoch, package.version, package.release)

        def __lt__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) < 0

        def __gt__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) > 0

        def __eq__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) == 0

        def __le__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) <= 0

        def __ge__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) >= 0

        def __ne__(self, other):
            return label_compare(self.evr_tuple, other.evr_tuple) != 0

    return Klass


def _keep_n_latest_rpms(rpms, n=1):
    """
    Keep n latest non-modular rpms.

    Arguments:
        rpms (List[Rpm]): Sorted, oldest goes first

    Keyword arguments:
        n (int): Number of non-modular package versions to keep

    Returns:
        None. The packages list is changed in-place
    """
    # Use a queue of n elements per arch
    pkgs_per_arch = defaultdict(lambda: deque(maxlen=n))

    for rpm in rpms:
        pkgs_per_arch[rpm.arch].append(rpm)

    latest_pkgs_per_arch = [pkg for pkg in chain.from_iterable(pkgs_per_arch.values())]

    rpms[:] = latest_pkgs_per_arch