# module imports
from os import getuid
from pwd import getpwuid
from pathlib import Path

# local imports
from ...common.logger import ask, error, log
from ...common.tar import TAR
from ...common.utils import resolve_path


class Module:
    def __init__(self, module_type: str, src: str):
        # type
        self.type = module_type
        self.src = src

        # tar
        self.tar = TAR(algorithm="gzip")

        # templates_dir
        self.template_path = str(resolve_path(resolve_path(__file__).absolute()).parent.parent) + f"/templates/{self.type}/{self.src}.tar.gz"

        # dict to make YAML
        self.dict = {}

        # submodule
        self.submodule = False

        # check if luzbuild currently exists
        if resolve_path("LuzBuild").exists():
            val = ask(f"A LuzBuild was found in the current working directory. Would you like to add this module as a submodule? (y/n)")
            if val == "":
                val = "n"
            if val.startswith("y"):
                self.submodule = True

        self.control = None

        if not self.submodule:
            # init control
            self.control = {}

            # ask for control values
            self.control["name"] = self.ask_for("name")
            self.control["id"] = self.ask_for("bundle ID", f'com.yourcompany.{self.control["name"].lower()}')
            self.control["version"] = self.ask_for("version", "1.0.0")
            self.control["author"] = self.ask_for("author", getpwuid(getuid())[0], dsc="Who")
            self.control["maintainer"] = self.control["author"]
            self.control["depends"] = self.ask_for("dependencies", dsc1="are", default="", extra_msg="Separated by a comma and a space").split(", ")
            if self.control["depends"] == [""]:
                self.control["depends"] = []
            self.control["architecture"] = self.ask_for("architecture", "iphoneos-arm64")

            # add control to dict
            self.dict["control"] = self.control

    def write_to_file(self, path: Path = None) -> None:
        """Write the dict to a file.

        :param Path path: The path to write to.
        """
        # resolve path
        path = resolve_path(f"{path}/luz.py")
        # extract archive to directory
        self.tar.decompress_archive(self.template_path, path.parent)
        # check for after_untar
        if hasattr(self, "after_untar"):
            self.after_untar()
        # format into Python
        py = "from luz import Control, Module\n\n"

        for k, v in self.dict.items():
            if k == "control":
                py += f"control = Control("
                for k1, v1 in v.items():
                    py += f"\n\t{k1}='{v1}', " if isinstance(v1, str) else f"\n\t{k1}={v1}, "
                py = py[:-2] + "\n)\n\n"
            else:
                py += f"modules = [\n    Module("
                for k1, v1 in v.items():
                    py += f"\n\t\t{k1}='{v1}', " if isinstance(v1, str) else f"\n\t\t{k1}={v1}, "
                py = py[:-2] + "\n\t)\n]"

        # write to file
        path.write_text(py)

        log(f"Successfully wrote to {path}.")

        # instructions
        if self.submodule:
            log(f"To add this module as a submodule, you can add Submodule(path=\"{path.parent}\") to your 'submodules' list in the parent project's `luz.py`.")

    def ask_for(self, key: str, default: str = None, dsc: str = "What", dsc1: str = "is", extra_msg: str = "") -> str:
        """Ask for a value.

        :param str key: The key to ask for.
        :param str default: The default value.
        :param str dsc: The descriptor of the question.
        :param str dsc1: The descriptor of the question.
        :return: The value.
        """
        if default is not None:
            val = ask(f"{dsc} {dsc1} this project's {key}?{f' ({extra_msg})' if extra_msg != '' else '' } (enter for '{default}')")
            if val == "":
                return default
        else:
            val = ask(f"{dsc} {dsc1} this project's {key}?{f' ({extra_msg})' if extra_msg != '' else '' }")
            if val == "":
                error("You must enter a value.")
                val = ask(f"{dsc} {dsc1} this {self.type}'s {key}?{f' ({extra_msg})' if extra_msg != '' else '' }")
        return val
