# module imports
from concurrent.futures import ThreadPoolExecutor, wait
from os import makedirs
from shutil import copytree, rmtree
from subprocess import check_output

# local imports
from ..common.deps import clone_headers, clone_libraries, logos
from ..common.logger import log
from ..common.utils import get_hash, resolve_path


class ModuleBuilder:
    """Module builder class."""

    def __init__(self, module, luz):
        """Initialize module builder.

        Args:
            module (Module): The module to build.
            luz (Luz): The luz object.
        """
        # variables
        self.module = module
        self.luz = luz
        self.meta = luz.meta
        self.control = luz.control

        # add necessary include files
        cloned_libs = str(clone_libraries(self.luz))
        self.module.include_dirs.append(str(clone_headers(self.luz)))
        self.module.library_dirs.append(cloned_libs)
        self.module.framework_dirs.append(cloned_libs)
        self.module.include_dirs.append(f"{self.meta.sdk}/usr/include")
        self.module.library_dirs.append(f"{self.meta.sdk}/usr/lib")
        self.module.framework_dirs.append(f"{self.meta.sdk}/System/Library/Frameworks")

        # add custom files
        self.module.include_dirs.append(f"{self.meta.storage}/headers")
        self.module.library_dirs.append(f"{self.meta.storage}/lib")
        self.module.framework_dirs.append(f"{self.meta.storage}/lib")

        # swift
        for file in self.module.files:
            if str(file).endswith(".swift"):
                self.module.library_dirs.append("/usr/lib/swift")
                self.module.library_dirs.append(f"{self.meta.sdk}/usr/lib/swift")
                break

        # private frameworks
        if self.module.private_frameworks != []:
            if resolve_path(f"{self.meta.sdk}/System/Library/PrivateFrameworks").exists():
                self.module.framework_dirs.append(f"{self.meta.sdk}/System/Library/PrivateFrameworks")
            else:
                raise Exception(f"Private frameworks are not available on the SDK being used. ({self.meta.sdk})")

        # directories
        self.logos_dir = resolve_path(f"{self.luz.build_dir}/logos-processed")
        self.obj_dir = resolve_path(f"{self.luz.build_dir}/obj/{self.module.name}")
        self.dylib_dir = resolve_path(f"{self.luz.build_dir}/dylib/{self.module.name}")
        self.bin_dir = resolve_path(f"{self.luz.build_dir}/bin/{self.module.name}")

        # fix install dir
        self.module.install_dir = self.module.install_dir.relative_to(self.module.install_dir.anchor)

        # files
        self.files = self.__hash_files(self.module.files, "executable" if self.module.type == "tool" else "dylib")

    def __hash_files(self, files, compile_type: str = "dylib"):
        """Hash source files, and check if their objects exist.

        :param list files: The list of files to hash.
        :param str type: The type of files to hash.
        """
        # make dirs
        if not self.obj_dir.exists():
            makedirs(self.obj_dir, exist_ok=True)

        files_to_compile = []

        # changed files
        changed = []
        # new hashes
        new_hashes = {}
        # arch count
        arch_count = len(self.meta.archs)
        # file path formatting
        for file in files:
            if not str(file).startswith("/"):
                file = f"{self.luz.path}/{file}"
            file_path = resolve_path(file)
            files_to_compile.append(file_path)

        # dylib
        if compile_type == "dylib":
            if not self.dylib_dir.exists():
                makedirs(self.dylib_dir, exist_ok=True)
        # executable
        elif compile_type == "executable":
            if not self.bin_dir.exists():
                makedirs(self.bin_dir, exist_ok=True)

        # loop files
        for file in files_to_compile:
            # get file hash
            if "hashlist" not in self.luz.build_info:
                self.luz.build_info["hashlist"] = {}
            fhash = self.luz.build_info["hashlist"].get(str(file))
            new_hash = get_hash(file)
            if fhash is None:
                changed.append(file)
            elif fhash == new_hash:
                # variables
                object_paths = resolve_path(f"{self.obj_dir}/*/{file.name}*-*.o")
                lipod_paths = resolve_path(f"{self.obj_dir}/*/{self.module.install_name}")
                if len(object_paths) < arch_count or len(lipod_paths) < arch_count:
                    changed.append(file)
            elif fhash != new_hash:
                changed.append(file)
            # add to new hashes
            new_hashes[str(file)] = new_hash

        # hashes
        self.luz.update_hashlist(new_hashes)

        # files list
        files = changed if self.module.only_compile_changed else files_to_compile

        # handle files not needing compilation
        if len(files) == 0:
            log(
                f'Nothing to compile for module "{self.module.name}".',
                "🔨",
                self.module.abbreviated_name,
                self.luz.lock,
            )
            return []

        files = files_to_compile

        # use logos on files
        if not self.logos_dir.exists() and list(filter(lambda x: ".x" in x, [str(f) for f in files])) != []:
            makedirs(self.logos_dir, exist_ok=True)
        files = logos(self.luz, self.module, files)

        # pool
        self.pool = ThreadPoolExecutor(max_workers=(len(files) * arch_count))

        # return files
        return files

    def __linker(self, compile_type: str = "dylib"):
        """Use a linker on the compiled files.

        :param str type: The type of files to link.
        """
        if compile_type == "dylib":
            out_name = resolve_path(f"{self.dylib_dir}/{self.module.install_name}")
        else:
            out_name = resolve_path(f"{self.bin_dir}/{self.module.install_name}")

        # check if linked files exist
        if len(self.files) == 0 and out_name.exists():
            return

        # log
        log(
            f'Linking compiled objects to "{self.module.install_name}"...',
            "🔗",
            self.module.abbreviated_name,
            self.luz.lock,
        )

        # build args
        build_flags = [
            "-fobjc-arc" if self.module.use_arc else "",
            f"-isysroot {self.meta.sdk}",
            f"-O{self.module.optimization}",
            ("-I" + " -I".join(self.module.include_dirs)) if self.module.include_dirs != [] else "",
            ("-L" + " -L".join(self.module.library_dirs)) if self.module.library_dirs != [] else "",
            ("-F" + " -F".join(self.module.framework_dirs)) if self.module.framework_dirs != [] else "",
            ("-l" + " -l".join(self.module.libraries)) if self.module.libraries != [] else "",
            ("-framework " + " -framework ".join(self.module.frameworks)) if self.module.frameworks != [] else "",
            ("-framework " + " -framework ".join(self.module.private_frameworks)) if self.module.private_frameworks != [] else "",
            f"-m{self.meta.platform}-version-min={self.meta.min_vers}",
            "-g" if self.meta.debug else "",
            f"-Wl,-install_name,{'/var/jb' if self.meta.rootless else ''}/{self.module.install_dir}/{self.module.install_name},-rpath,{'/var/jb' if self.meta.rootless else ''}/usr/lib/,-rpath,{'/var/jb' if self.meta.rootless else ''}/Library/Frameworks/",
        ]
        build_flags.extend(self.module.warnings)
        build_flags.extend(self.module.linker_flags)
        # add dynamic lib to args
        if compile_type == "dylib":
            build_flags.append("-dynamiclib")
        # compile for each arch
        # format platform
        platform = "ios" if self.meta.platform == "iphoneos" else self.meta.platform
        for arch in self.meta.archs:
            try:
                # strings
                strings = []
                for file in resolve_path(f"{self.obj_dir}/{arch}/*.o"):
                    strings.append(str(file))
                # arch
                arch_formatted = f"-target {arch}-apple-{platform}{self.meta.min_vers}"
                self.luz.cmd.exec_output(f"{self.meta.cc} {' '.join(strings)} -o {self.obj_dir}/{arch}/{self.module.install_name} {' '.join(build_flags)} {arch_formatted}")
            except Exception as e:
                print(e)
                return f'An error occured when trying to link files for module "{self.module.name}" for architecture "{arch}".'

        # link
        try:
            compiled = [f"{self.obj_dir}/{arch}/{self.module.install_name}" for arch in self.meta.archs]
            self.luz.cmd.exec_no_output(f"{self.meta.lipo} -create -output {out_name} {' '.join(compiled)}")
        except:
            return f'An error occured when trying to lipo files for module "{self.module.name}".'

        if compile_type == "executable" and self.meta.release:
            try:
                self.luz.cmd.exec_no_output(f"{self.meta.strip} {out_name}")
            except:
                return f'An error occured when trying to strip "{out_name}" for module "{self.module.name}".'

        try:
            # run ldid
            self.luz.cmd.exec_no_output(f"{self.meta.ldid} {' '.join(self.module.codesign_flags)} {out_name}")
        except:
            return f'An error occured when trying codesign "{out_name}" for module "{self.module.name}".'

    def __handle_logos(self):
        """Handle files that have had Logos ran on them."""
        self.files_paths = []
        for file in self.files:
            new_path = ""
            # handle logos files
            if file.get("logos") == True:
                # new path
                new_path = file.get("new_path")
                # set original path
                orig_path = file.get("old_path")
                # include path
                include_path = "/".join(str(orig_path).split("/")[:-1])
                # add it to include if it's not already there
                if include_path not in self.module.include_dirs:
                    self.module.include_dirs.append(include_path)
            # handle normal files
            else:
                new_path = file.get("path")

            # add to files paths
            self.files_paths.append(new_path)

    def __compile_file(self, file):
        # log
        if file.get("old_path") is not None:
            file_formatted = str(file.get("old_path")).replace(str(self.luz.path.absolute()), "")
            if file_formatted != str(file.get("old_path")):
                file_formatted = "/".join(file_formatted.split("/")[1:])
            msg = f'Compiling "{file_formatted}"...'
        else:
            file_formatted = str(file.get("path")).replace(str(self.luz.path.absolute()), "")
            if file_formatted != str(file.get("path")):
                file_formatted = "/".join(file_formatted.split("/")[1:])
            msg = f'Compiling "{file_formatted}"...'

        log(msg, "🔨", self.module.abbreviated_name, self.luz.lock)

        file = list(
            filter(
                lambda x: x == file.get("new_path") or x == file.get("path"),
                self.files_paths,
            )
        )[0]

        # compile file
        try:
            if str(file).endswith(".swift"):
                files_minus_to_compile = list(
                    filter(
                        lambda x: x != file and str(x).endswith(".swift"),
                        self.files_paths,
                    )
                )
                fmtc = [str(x) for x in files_minus_to_compile]
                futures = [self.pool.submit(self.__compile_swift_arch, file, fmtc, x) for x in self.meta.archs]
            else:
                futures = [self.pool.submit(self.__compile_c_arch, file, x) for x in self.meta.archs]
            self.wait(futures)

            # check results
            for future in futures:
                if future.result() is not None:
                    return f'An error occured when attempting to compile for module "{self.module.name}".'

        except:
            return f'An error occured when attempting to compile for module "{self.module.name}".'

    def __compile_swift_arch(self, file, fmtc: list, arch: str):
        # format platform
        platform = "ios" if self.meta.platform == "iphoneos" else self.meta.platform
        # arch
        arch_formatted = f"-target {arch}-apple-{platform}{self.meta.min_vers}"
        # outname
        out_name = f"{self.obj_dir}/{arch}/{file.name}-{self.luz.now}"
        # define build flags
        build_flags = [
            "-frontend",
            "-c",
            f"-module-name {self.module.name}",
            f'-sdk "{self.meta.sdk}"',
            ("-I" + " -I".join(self.module.include_dirs)) if self.module.include_dirs != [] else "",
            ("-import-objc-header" + " -import-objc-header".join(self.module.bridging_headers)) if self.module.bridging_headers != [] else "",
            arch_formatted,
            f"-emit-module-path {out_name}.swiftmodule",
            f"-o {out_name}.o",
            "-g" if self.meta.debug else "",
            "-primary-file",
        ]
        build_flags.extend(self.module.swift_flags)
        rmtree(
            f"{self.obj_dir}/{arch}/{file.name}-*",
            ignore_errors=True,
        )
        # compile with swift using build flags
        try:
            self.luz.cmd.exec_output(f"{self.meta.swift} {' '.join(build_flags)} {file} {' '.join(fmtc)}")
        except:
            return f'An error occured when trying to compile "{file}" for module "{self.module.name}".'

    def __compile_c_arch(self, file, arch: str):
        # format platform
        platform = "ios" if self.meta.platform == "iphoneos" else self.meta.platform
        # arch
        arch_formatted = f"-target {arch}-apple-{platform}{self.meta.min_vers}"
        # outname
        out_name = f"{self.obj_dir}/{arch}/{file.name}-{self.luz.now}.o"
        build_flags = [
            "-fobjc-arc" if self.module.use_arc else "",
            f"-isysroot {self.meta.sdk}",
            f"-O{self.module.optimization}",
            arch_formatted,
            ("-I" + " -I".join(self.module.include_dirs)) if self.module.include_dirs != [] else "",
            f"-m{self.meta.platform}-version-min={self.meta.min_vers}",
            "-g" if self.meta.debug else "",
            f"-o {out_name}",
            f'-DLUZ_PACKAGE_VERSION=\\"{self.control.version}\\"' if self.control else "",
            f'-DLUZ_INSTALL_PREFIX=\\"/var/jb\\"' if self.meta.rootless else f'-DLUZ_INSTALL_PREFIX=\\"\\"',
            "-c",
        ]
        build_flags.extend(self.module.c_flags)
        build_flags.extend(self.module.warnings)
        rmtree(
            f"{self.obj_dir}/{arch}/{file.name}-*",
            ignore_errors=True,
        )
        # compile with clang using build flags
        try:
            self.luz.cmd.exec_output(f"{self.meta.cc} {' '.join(build_flags)} {file}")
        except:
            return f'An error occured when attempting to compile "{file}" for module "{self.module.name}".'

    def __stage(self):
        """Stage a generic deb to be packaged."""
        # log
        log(f"Staging...", "📦", self.module.abbreviated_name, self.luz.lock)
        # before stage
        if self.module.before_stage:
            self.module.before_stage()
        # dirs to make
        dirtocopy = self.meta.root_dir / self.module.install_dir
        # make proper dirs
        if not dirtocopy.parent.exists():
            makedirs(dirtocopy.parent, exist_ok=True)
        # dir of linked file
        if self.module.type == "tool": linked = self.bin_dir
        else: linked = self.dylib_dir
        copytree(linked, dirtocopy, dirs_exist_ok=True)
        # after stage
        if self.module.after_stage:
            self.module.after_stage()

    def compile(self):
        """Compile module."""
        # handle logos
        self.__handle_logos()
        # clean arch dirs
        for arch in self.meta.archs:
            for x in self.files_paths:
                check_output(f"rm -rf {self.obj_dir}/{arch}/{x.name}-*", shell=True)
            makedirs(f"{self.obj_dir}/{arch}", exist_ok=True)
        # compile files
        futures = [self.luz.pool.submit(self.__compile_file, file) for file in self.files]
        self.wait(futures)
        for result in futures:
            if result.result() is not None:
                return result.result()
        # link files
        # get compile type
        compile_type = "executable" if self.module.type == "tool" else "dylib"
        linker_results = self.__linker(compile_type=compile_type)
        if linker_results is not None:
            return linker_results
        # stage deb
        if self.meta.pack:
            try:
                stage = self.__getattribute__("stage")
            except:
                stage = self.__stage
            stage_result = stage()
            if stage_result is not None:
                return stage_result

    def wait(self, thread):
        wait(thread)
