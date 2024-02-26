#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

"""
Create and validate MicroPython package.json files based on Python setup.py
files
"""

import json
import logging
import secrets
from struct import pack
import sys
from tokenize import String
import tomllib
import importlib
from pathlib import Path
from typing import List, Optional, Tuple

from changelog2version.extract_version import ExtractVersion
from .setup2upypackage import Setup2uPyPackageError
from deepdiff import DeepDiff
import setuptools


class Pyproject2uPyPackage(object):
    """Handle MicroPython package JSON creation and validation"""

    def __init__(
        self,
        project_config_file: Path,
        package_file: Optional[Path],
        package_changelog_file: Optional[Path],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Init Pyproject2uPyPackage class

        :param      project_config_file:    The pyproject.toml file
        :type       project_config_file:    Path
        :param      package_file:  The package.json file
        :type       package_file:  Optional[Path]
        :param      package_file:  The package changelog file
        :type       package_file:  Optional[Path]
        :param      logger:        Logger object
        :type       logger:        Optional[logging.Logger]
        """
        if logger is None:
            logger = self._create_logger()
        self._logger = logger

        self._project_config_file = project_config_file
        self._package_file = package_file
        self._package_changelog_file = package_changelog_file

        self._setup_data = {}
        self._root_dir = self._project_config_file.parent

        self._setup_data = self._parse_pyproject_file_content()

    @staticmethod
    def _create_logger(logger_name: str | None = None) -> logging.Logger:
        """
        Create a logger

        :param      logger_name:  The logger name
        :type       logger_name:  str, optional

        :returns:   Configured logger
        :rtype:     logging.Logger
        """
        custom_format = (
            "[%(asctime)s] [%(levelname)-8s] [%(filename)-15s @"
            " %(funcName)-15s:%(lineno)4s] %(message)s"
        )

        # configure logging
        logging.basicConfig(level=logging.INFO, format=custom_format, stream=sys.stdout)

        if logger_name and (isinstance(logger_name, str)):
            logger = logging.getLogger(logger_name)
        else:
            logger = logging.getLogger(__name__)

        # set the logger level to DEBUG if specified differently
        logger.setLevel(logging.DEBUG)

        return logger

    def _parse_pyproject_file_content(self) -> dict:
        with open(self._project_config_file, "rb") as f:
            return tomllib.load(f)

    def gensym(self, length=32, prefix="gensym_"):
        """
        generates a fairly unique symbol, used to make a module name,
        used as a helper function for load_module

        :return: generated symbol
        """
        return "test-module-name"

    def load_module(self, source, module_name=None):
        """
        reads file source and loads it as a module

        :param source: file to load
        :param module_name: name of module to register in sys.modules
        :return: loaded module
        """

        if module_name is None:
            module_name = self.gensym()

        spec = importlib.util.spec_from_file_location(module_name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module

    @property
    def package_version(self) -> str:
        """
        Get version of package based on project version

        :returns:   Package version
        :rtype:     str
        """

        version = (
            self._setup_data.get("tool", {})
            .get("setuptools", {})
            .get("dynamic", {})
            .get("version", "-1.-1.-1")
        )

        if version["attr"]:
            version_module = ".".join(version["attr"].split(".")[:-1])
            version_attr = version["attr"].split(".")[-1]
            package_path = self._find_packages(version_module)[version_module]
            m = self.load_module(package_path + ".py")

            return getattr(m, version_attr)
        else:
            self._logger.warning("Unable to identify package 'version'")
            return "-1.-1.-1"

    @property
    def package_changelog_version(self) -> str:
        """
        Get package changelog version

        :returns:   Package changelog version
        :rtype:     str
        """
        if self._package_changelog_file:
            ev = ExtractVersion(logger=self._logger)

            version_line = ev.parse_changelog(
                changelog_file=self._package_changelog_file
            )
            semver_string = ev.parse_semver_line(release_version_line=version_line)

            return semver_string
        else:
            self._logger.warning("No package changelog file specified")
            return "-1.-1.-1"

    @property
    def package_deps(self) -> List[str]:
        """
        Get dependencies of package based on pyproject.toml

        :returns:   Package dependencies
        :rtype:     List[str]
        """

        dependencies = self._setup_data.get('project', {}).get('dependencies', None)
        if dependencies:
            return dependencies
        else:
            self._logger.warning("No 'dependencies' key found in setup data dict")
            return []

    @property
    def package_url(self) -> str:
        """
        Get URL of package based on pyproject.toml "urls[Source]" entry.

        :returns:   Package URL based on pyproject.toml "urls[Source]" entry
        :rtype:     str
        """

        if self._setup_data.get("project", []):
            return self._setup_data.get("project", [])["urls"]["Source"]
        else:
            self._logger.warning("No 'urls[Source]' key found in setup data dict")
            raise SystemExit("Project URL is mandatory")

    def _find_packages(self, package_name: str | None = None) -> dict[str, str]:
        packages_setup = self._setup_data.get("tool", {}).get("setuptools", {}).get("packages", {})
        package_paths = {}

        if isinstance(packages_setup, list):
            packages = packages_setup if package_name is None else [package_name]
            package_dir = (
                self._setup_data.get("tool", {}).get("setuptools", {}).get("package-dir", {"": "."})
            )

            for package in packages:
                package_path = setuptools.discovery.find_package_path(package, package_dir, self._root_dir)
                package_paths[package] = package_path

        else:
            packages_setup = []
            find = (
                self._setup_data.get("tool", {})
                .get("setuptools", {})
                .get("packages", {})
                .get("find", {})
            )
            where = find.get("where", ["."])

            for search_dir in where:
                packages = [package_name] if package_name is not None else setuptools.find_namespace_packages(
                    where=search_dir,
                    include=find.get("include", ("*",)),
                    exclude=find.get("exclude", ()),
                )

                for package in packages:
                    package_path = setuptools.discovery.find_package_path(package, {"": search_dir}, self._root_dir)
                    package_paths[package] = package_path

        return package_paths

    @property
    def package_files(self, package: str | None = None) -> List[str]:
        """
        Get packages based on "packages" entry.

        :returns:   Packages based on "packages" entry
        :rtype:     List[str]
        """

        all_files = []
        package_paths = self._find_packages()

        for info in package_paths.items():
            package = info[0]
            search_dir = str(Path(info[1]).relative_to(self._root_dir)) + "/*.py"

            p = self._root_dir.glob(search_dir)
            files = [x.relative_to(self._root_dir) for x in p if x.is_file()]
            all_files.extend(files)

        return all_files

    @property
    def data_files(self) -> List[str]:
        """Not implemented yet"""
        return []

    def _create_url_elements(self, package_files: List[str], url: str) -> List[str]:
        """
        Create URLs to all package elements.

        :param      package_files:  The package files
        :type       package_files:  List[str]
        :param      url:            The URL
        :type       url:            str

        :returns:   List of URLs to download the package files
        :rtype:     List[str]
        """
        urls = []

        for file in package_files:
            this_url = [str(file), str(Path(url) / file)]
            self._logger.debug("File elements: {}: {}".format(file, this_url))
            urls.append(this_url)

        return urls

    @property
    def package_data(self) -> dict:
        """
        Get mip compatible package data

        :returns:   mip compatible package.json data
        :rtype:     dict
        """
        urls = []
        package_data = {"urls": [], "deps": [], "version": "-1.-1.-1"}
        package_files = self.package_files
        if self._package_changelog_file:
            version = self.package_changelog_version
        else:
            version = self.package_version
        install_requires = self.package_deps
        data_files = self.data_files
        url = self.package_url.replace("https://github.com/", "github:")
        for x in [package_files, data_files]:
            urls.extend(self._create_url_elements(package_files=x, url=url))

        self._logger.debug("version: {}".format(version))
        self._logger.debug("install_requires: {}".format(install_requires))
        self._logger.debug("package_files: {}".format(package_files))
        self._logger.debug("data_files: {}".format(data_files))
        self._logger.debug("url: {}".format(url))
        self._logger.debug("urls: {}".format(urls))

        package_data["urls"] = urls
        package_data["deps"] = install_requires
        package_data["version"] = version

        return package_data

    @property
    def package_json_data(self) -> dict:
        """
        Get package.json data

        :returns:   Existing package.json data
        :rtype:     dict
        """
        existing_data = {}

        if self._package_file:
            with open(self._package_file, "r") as f:
                existing_data = json.load(f)
        else:
            raise Setup2uPyPackageError("No package.json data specified")

        return existing_data

    def validate(
        self,
        ignore_version: bool = False,
        ignore_deps: bool = False,
        ignore_boot_main: bool = False,
    ) -> bool:
        """
        Validate existing package.json with setup.py based data

        :param      ignore_version:     Flag to ignore the version
        :type       ignore_version:     bool
        :param      ignore_deps:        Flag to ignore the dependencies
        :type       ignore_deps:        bool
        :param      ignore_boot_main:   Flag to ignore the main and boot files
        :type       ignore_boot_main:   bool

        :returns:   Result of validation, True on success, False otherwise
        :rtype:     bool
        """
        # list of URL entries might be sorted differently
        package_json_data = dict(self.package_json_data)
        package_data = dict(self.package_data)

        if ignore_version:
            package_json_data.pop("version", None)
            package_data.pop("version", None)

        if ignore_deps:
            package_json_data.pop("deps", None)
            package_data.pop("deps", None)

        if ignore_boot_main:
            package_json_data["urls"] = self._exclude_package_files(
                package_files=package_json_data.get("urls", [])
            )

            package_data["urls"] = self._exclude_package_files(
                package_files=package_data.get("urls", [])
            )

        package_json_data.get("urls", []).sort()
        package_data.get("urls", []).sort()

        return package_json_data == package_data

    def _exclude_package_files(
        self,
        package_files: List[Tuple[str, str]],
        excludes: List[str] = ["boot.py", "main.py"],
    ) -> List[Tuple[str, str]]:
        """
        Exclude elements of a list if the first element matches an exclude str

        :param      package_files:  The package files
        :type       package_files:  List[Tuple[str, str]]
        :param      excludes:       The list of excludes
        :type       excludes:       List[str]

        :returns:   List without elements matching the exclude list
        :rtype:     List[Tuple[str, str]]
        """
        return [ele for ele in package_files if not any(i in ele[0] for i in excludes)]

    @property
    def validation_diff(self) -> DeepDiff:
        """
        Get difference of package.json and setup.py

        :returns:   The deep difference.
        :rtype:     DeepDiff
        """
        return DeepDiff(self.package_data, self.package_json_data)

    def create(self, output_path: Optional[Path] = None, pretty: bool = True) -> None:
        """
        Create package.json file in same directory as setup.py

        :param      output_path:  The output path
        :type       output_path:  Optional[Path]
        :param      pretty:       Flag to use an indentation of 4
        :type       pretty:       bool
        """
        if not output_path:
            if self._package_file:
                output_path = self._package_file
            else:
                output_path = self._project_config_file.parent / "package.json"
                self._logger.info(
                    "No package.json data specified, using setup.py directory"
                )

        with open(output_path, "w") as file:
            if pretty:
                file.write(json.dumps(self.package_data, indent=4))
            else:
                file.write(json.dumps(self.package_data))

        self._logger.debug("Created {}".format(output_path))
