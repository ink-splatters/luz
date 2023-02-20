# module imports
from os import makedirs
from shutil import copytree, rmtree
from time import time

# local imports
from .module import Module
from ...common.utils import resolve_path


class Preferences(Module):
    def __init__(self, **kwargs):
        """Preferences module class

        :param dict module: Module dictionary to build
        :param str key: Module key name
        :param LuzBuild luzbuild: Luzbuild class
        """
        # current time
        self.now = time()
        # kwargs parsing
        module = kwargs.get("module")
        super().__init__(module, kwargs.get("key"), kwargs.get("luzbuild"))

    def __compile_prefs_file(self, file):
        """Compile a preferences file.

        :param str file: The file to compile.
        """
        files_minus_to_compile = list(filter(lambda x: x != file and str(x).endswith(".swift"), self.files))
        # compile file
        try:
            if str(file).endswith(".swift"):
                # define build flags
                build_flags = [
                    "-frontend",
                    "-c",
                    f"-module-name {self.name}",
                    f'-sdk "{self.luzbuild.sdk}"',
                    self.include,
                    self.library_dirs,
                    self.framework_dirs,
                    self.libraries,
                    self.frameworks,
                    self.private_frameworks,
                    self.swift_flags,
                    "-g" if self.luzbuild.debug else "",
                    self.bridging_headers,
                ]
                # format platform
                platform = "ios" if self.luzbuild.platform == "iphoneos" else self.luzbuild.platform
                for arch in self.luzbuild.archs:
                    rmtree(
                        f"{self.dir}/obj/{self.name}/{arch}/{file.name}-*",
                        ignore_errors=True,
                    )
                    out_name = f"{self.dir}/obj/{self.name}/{arch}/{file.name}-{self.now}"
                    # arch
                    arch_formatted = f"-target {arch}-apple-{platform}{self.luzbuild.min_vers}"
                    # compile with swift using build flags
                    self.luzbuild.swift_compiler.compile(
                        [file] + files_minus_to_compile,
                        outfile=out_name + ".o",
                        args=build_flags
                        + [
                            arch_formatted,
                            f"-emit-module-path {out_name}.swiftmodule",
                            "-primary-file",
                        ],
                    )
            else:
                for arch in self.luzbuild.archs:
                    rmtree(
                        f"{self.dir}/obj/{self.name}/{arch}/{file.name}-*",
                        ignore_errors=True,
                    )
                    out_name = f"{self.dir}/obj/{self.name}/{arch}/{file.name}-{self.now}.o"
                    build_flags = [
                        "-fobjc-arc" if self.arc else "",
                        f"-isysroot {self.luzbuild.sdk}",
                        self.warnings,
                        f"-O{self.optimization}",
                        f"-arch {arch}",
                        self.include,
                        f"-m{self.luzbuild.platform}-version-min={self.luzbuild.min_vers}",
                        "-g" if self.luzbuild.debug else "",
                        self.c_flags,
                        "-c",
                    ]
                    # compile with clang using build flags
                    self.luzbuild.c_compiler.compile(file, out_name, build_flags)
        except Exception as e:
            print(e)
            return f'An error occured when attempting to compile for module "{self.name}".'

    def __stage(self):
        """Stage a deb to be packaged."""
        # dirs to make
        dirtomake = resolve_path(f"{self.dir}/_/Library/PreferenceBundles/") if not self.luzbuild.rootless else resolve_path(f"{self.dir}/_/var/jb/Library/PreferenceBundles/")
        dirtocopy = (
            resolve_path(f"{self.dir}/_/Library/PreferenceBundles/{self.name}.bundle")
            if not self.luzbuild.rootless
            else resolve_path(f"{self.dir}/_/var/jb/Library/PreferenceBundles/{self.name}.bundle")
        )
        # make proper dirs
        if not dirtomake.exists():
            makedirs(dirtomake, exist_ok=True)
        copytree(f"{self.dir}/dylib/{self.name}", dirtocopy, dirs_exist_ok=True)
        # copy resources
        resources_path = resolve_path(f"{self.luzbuild.path}/Resources")
        if not resources_path.exists():
            return f'Resources/ folder for "{self.name}" does not exist'
        # copy resources
        copytree(resources_path, dirtocopy, dirs_exist_ok=True)

    def compile(self):
        """Compile."""
        for arch in self.luzbuild.archs:
            rmtree(f"{self.dir}/obj/{self.name}/{arch}", ignore_errors=True)
            makedirs(f"{self.dir}/obj/{self.name}/{arch}", exist_ok=True)
        # compile files
        compile_results = self.luzbuild.pool.map(self.__compile_prefs_file, self.files)
        for result in compile_results:
            if result is not None:
                return result

        # link files
        linker_results = self.linker()
        if linker_results is not None:
            return linker_results
        # stage deb
        if self.luzbuild.should_pack:
            stage_results = self.__stage()
            if stage_results is not None:
                return stage_results
