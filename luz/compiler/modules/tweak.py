# module imports
from os import makedirs
from shutil import copytree, rmtree
from subprocess import check_output
from time import time

# local imports
from ..deps import logos
from ...common.logger import warn
from .module import Module
from ...common.utils import get_hash, resolve_path


class Tweak(Module):
    def __init__(self, **kwargs):
        """Tweak module class
        
        :param dict module: Module dictionary to build
        :param str key: Module key name
        :param LuzBuild luzbuild: Luzbuild class
        """
        # current time
        self.now = time()
        # kwargs parsing
        module = kwargs.get('module')
        # files
        files = module.get('files') if type(module.get(
            'files')) is list else [module.get('files')]
        super().__init__(module, kwargs.get('key'), kwargs.get('luzbuild'))
        # directories
        self.obj_dir = resolve_path(f'{self.dir}/obj/{self.name}')
        self.logos_dir = resolve_path(f'{self.dir}/logos-processed')
        self.dylib_dir = resolve_path(f'{self.dir}/dylib/{self.name}')
        self.files = self.__hash_files(files)
    

    def __hash_files(self, files: list) -> list:
        """Hash the files of the module, and compare them to their old hashes.

        :param list files: Files to hash.
        :return: The list of changed files.
        """
        # make dirs
        if not self.obj_dir.exists():
            makedirs(self.obj_dir, exist_ok=True)
        
        if not self.logos_dir.exists():
            makedirs(self.logos_dir, exist_ok=True)
        
        if not self.dylib_dir.exists():
            makedirs(self.dylib_dir, exist_ok=True)
            
        files_to_compile = []
            
        # file path formatting
        for file in files:
            if not file.startswith('/'): file = f'{self.luzbuild.path}/{file}'
            file_path = resolve_path(file)
            if type(file_path) is list:
                for f in file_path:
                    files_to_compile.append(f)
            else:
                files_to_compile.append(file_path)

        # changed files
        changed = []
        # new hashes
        new_hashes = {}
        # arch count
        arch_count = len(self.luzbuild.archs)
        # loop files
        for file in files_to_compile:
            # get file hash
            fhash = self.luzbuild.hashlist.get(str(file))
            new_hash = get_hash(file)
            if fhash is None: changed.append(file)
            elif fhash == new_hash:
                # variables
                object_paths = resolve_path(f'{self.dir}/obj/{self.name}/*/{file.name}*-*.o')
                dylib_paths = resolve_path(f'{self.dir}/obj/{self.name}/*/{self.name}.dylib')
                if len(object_paths) < arch_count or len(dylib_paths) < arch_count:
                    print('\n', file, 'OBJS:', object_paths, 'DYLIBS:', dylib_paths)
                    changed.append(file)
            elif fhash != new_hash: changed.append(file)
            # add to new hashes
            new_hashes[str(file)] = new_hash

        # hashes
        self.luzbuild.update_hashlist(new_hashes)

        # files list
        files = changed if self.only_compile_changed else files_to_compile
        
        # handle files not needing compilation
        if len(files) == 0:
            self.log(f'Nothing to compile for module "{self.name}".')
            return []
    
        files = files_to_compile

        # use logos files if necessary
        if filter(lambda x: '.x' in x, files) != []:
            files = logos(self.luzbuild, files)

        # return files
        return files


    def __linker(self):
        """Use a linker on the compiled files."""

        if len(self.files) == 0 and resolve_path(f'{self.dir}/dylib/{self.name}/{self.name}.dylib').exists():
            return

        self.log_stdout(f'Linking compiled files to "{self.name}.dylib"...')

        for arch in self.luzbuild.archs:
            try:
                # define compiler flags
                build_flags = ['-fobjc-arc' if self.arc else '',
                            f'-isysroot {self.luzbuild.sdk}', self.warnings, f'-O{self.optimization}', f'-arch {arch}', self.include, self.library_dirs, self.framework_dirs,self.libraries, self.frameworks, self.private_frameworks, f'-m{self.luzbuild.platform}-version-min={self.luzbuild.min_vers}', '-dynamiclib', self.c_flags]
                self.luzbuild.c_compiler.compile(resolve_path(
                    f'{self.dir}/obj/{self.name}/{arch}/*.o'), outfile=f'{self.dir}/obj/{self.name}/{arch}/{self.name}.dylib', args=build_flags)
            except:
                return f'An error occured when trying to link files for module "{self.name}" for architecture "{arch}".'

        # link
        try:
            check_output(
                f'{self.luzbuild.lipo} -create -output {self.dir}/dylib/{self.name}/{self.name}.dylib {self.dir}/obj/{self.name}/*/{self.name}.dylib', shell=True)
        except:
            return f'An error occured when trying to lipo files for module "{self.name}".'
        
        try:
            # fix rpath
            rpath = '/var/jb/Library/Frameworks/' if self.luzbuild.rootless else '/Library/Frameworks'
            check_output(f'{self.luzbuild.install_name_tool} -add_rpath {rpath} {self.dir}/dylib/{self.name}/{self.name}.dylib', shell=True)
        except:
            return f'An error occured when trying to add rpath to "{self.dir}/dylib/{self.name}/{self.name}.dylib" for module "{self.name}".'
        
        try:
            # run ldid
            check_output(f'{self.luzbuild.ldid} {self.entflag}{self.entfile} {self.dir}/dylib/{self.name}/{self.name}.dylib', shell=True)
        except:
            return f'An error occured when trying to codesign "{self.dir}/dylib/{self.name}/{self.name}.dylib". ({self.name})'
        
        self.remove_log_stdout(f'Linking compiled files to "{self.name}.dylib"...')
        

    def __compile_tweak_file(self, file):
        """Compile a tweak file.
        
        :param str file: The file to compile.
        """
        file = list(filter(lambda x: x == file.get('new_path') or x == file.get('path'), self.files_paths))[0]
        files_minus_to_compile = list(filter(lambda x: x != file and str(x).endswith('.swift'), self.files_paths))
        # compile file
        try:
            if str(file).endswith('.swift'):
                # define build flags
                build_flags = ['-frontend', '-c', f'-module-name {self.name}', f'-sdk "{self.luzbuild.sdk}"', self.include, self.library_dirs, self.framework_dirs,self.libraries, self.frameworks, self.private_frameworks, self.swift_flags, self.bridging_headers]
                # format platform
                platform = 'ios' if self.luzbuild.platform == 'iphoneos' else self.luzbuild.platform
                for arch in self.luzbuild.archs:
                    rmtree(f'{self.dir}/obj/{self.name}/{arch}/{file.name}-*', ignore_errors=True)
                    out_name = f'{self.dir}/obj/{self.name}/{arch}/{file.name}-{self.now}'
                    # arch
                    arch_formatted = f'-target {arch}-apple-{platform}{self.luzbuild.min_vers}'
                    # compile with swift using build flags
                    self.luzbuild.swift_compiler.compile([file] + files_minus_to_compile, outfile=out_name+'.o', args=build_flags+[arch_formatted, f'-emit-module-path {out_name}.swiftmodule', '-primary-file'])
            else:
                for arch in self.luzbuild.archs:
                    rmtree(
                        f'{self.dir}/obj/{self.name}/{arch}/{file.name}-*', ignore_errors=True)
                    out_name = f'{self.dir}/obj/{self.name}/{arch}/{file.name}-{self.now}.o'
                    build_flags = ['-fobjc-arc' if self.arc else '',
                                f'-isysroot {self.luzbuild.sdk}', self.warnings, f'-O{self.optimization}', f'-arch {arch}', self.include, f'-m{self.luzbuild.platform}-version-min={self.luzbuild.min_vers}', self.c_flags, '-c']
                    # compile with clang using build flags
                    self.luzbuild.c_compiler.compile(file, out_name, build_flags)
            
        except:
            return f'An error occured when attempting to compile for module "{self.name}".'
            
            
    def __stage(self):
        """Stage a deb to be packaged."""
        # dirs to make
        if self.install_dir is None:
            dirtomake = resolve_path(
                f'{self.dir}/_/Library/MobileSubstrate/') if not self.luzbuild.rootless else resolve_path(f'{self.dir}/_/var/jb/usr/lib/')
            dirtocopy = resolve_path(f'{self.dir}/_/Library/MobileSubstrate/DynamicLibraries/') if not self.luzbuild.rootless else resolve_path(
                f'{self.dir}/_/var/jb/usr/lib/TweakInject')
        else:
            if self.luzbuild.rootless:
                warn(f'Custom install directory for module "{self.name}" was specified, and rootless is enabled. Prefixing path with /var/jb.')
            self.install_dir = resolve_path(self.install_dir)
            dirtomake = resolve_path(f'{self.dir}/_/{self.install_dir.parent}') if not self.luzbuild.rootless else resolve_path(
                f'{self.dir}/_/var/jb/{self.install_dir.parent}')
            dirtocopy = resolve_path(f'{self.dir}/_/{self.install_dir}') if not self.luzbuild.rootless else resolve_path(
                f'{self.dir}/_/var/jb/{self.install_dir}')
        # make proper dirs
        if not dirtomake.exists():
            makedirs(dirtomake, exist_ok=True)
        copytree(f'{self.dir}/dylib/{self.name}', dirtocopy, dirs_exist_ok=True)
        with open(f'{dirtocopy}/{self.name}.plist', 'w') as f:
            filtermsg = 'Filter = {\n'
            # bundle filters
            if self.filter.get('bundles') is not None:
                filtermsg += '    Bundles = ( '
                for filter in self.filter.get('bundles'):
                    filtermsg += f'"{filter}", '
                filtermsg = filtermsg[:-2] + ' );\n'
            # executables filters
            if self.filter.get('executables') is not None:
                filtermsg += '    Executables = ( '
                for executable in self.filter.get('executables'):
                    filtermsg += f'"{executable}", '
                filtermsg = filtermsg[:-2] + ' );\n'
            filtermsg += '};'
            f.write(filtermsg)
    

    def compile(self):
        """Compile."""
        for arch in self.luzbuild.archs:
            rmtree(f'{self.dir}/obj/{self.name}/{arch}', ignore_errors=True)
            makedirs(f'{self.dir}/obj/{self.name}/{arch}', exist_ok=True)
        self.files_paths = []
        for file in self.files:
            new_path = ''
            # handle logos files
            if file.get('logos') == True:
                # new path
                new_path = file.get('new_path')
                # set original path
                orig_path = file.get('old_path')
                # include path
                include_path = '/'.join(str(orig_path).split('/')[:-1])
                # add it to include if it's not already there
                if include_path not in self.include:
                    self.include += ' -I' + include_path
            else:
                new_path = file.get('path')
            # handle normal files
            self.files_paths.append(new_path)
        # compile files
        compile_results = self.luzbuild.pool.map(self.__compile_tweak_file, self.files)
        for result in compile_results:
            if result is not None:
                return result

        # link files
        linker_results = self.__linker()
        if linker_results is not None:
            return linker_results
        # stage deb
        self.__stage()
