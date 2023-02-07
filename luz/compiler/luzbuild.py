# module imports
from atexit import register
from multiprocessing.pool import ThreadPool
from os import makedirs
from pathlib import Path
from pyclang import CCompiler, SwiftCompiler
from pydeb import Pack
from shutil import copytree, rmtree
from subprocess import getoutput
from threading import Lock
from time import time
from yaml import safe_load

# local imports
from ..common.logger import error, log, log_stdout, remove_log_stdout
from ..common.utils import cmd_in_path, get_from_cfg, get_from_luzbuild, get_luz_storage, resolve_path, setup_luz_dir
from .modules.modules import assign_module


class LuzBuild:
    def __init__(self, clean: bool = False, path_to_file: str = 'LuzBuild', inherit: object = None):
        """Parse the luzbuild file.
        
        :param str path_to_file: The path to the luzbuild file.
        """
        # start
        self.time = time()

        # to inherit
        self.to_inherit = inherit

        # path
        self.path = resolve_path(str(path_to_file).split('LuzBuild')[0])

        if inherit is None:
            # module path
            module_path = resolve_path(resolve_path(__file__).absolute()).parent

            # read default config values
            with open(f'{module_path}/config/defaults.yaml') as f: self.defaults = safe_load(f)
        
        # open and parse luzbuild file
        with open(path_to_file) as f: self.luzbuild = safe_load(f)
        
        # exit if failed
        if self.luzbuild is None or self.luzbuild == {}:
            error('Failed to parse LuzBuild file.')
            exit(1)
        
        # clean
        if clean:
            rmtree('.luz', ignore_errors=True)

        # dir
        self.dir = setup_luz_dir()

        # pool
        self.pool = ThreadPool()

        # register pool close
        register(self.pool.close)
        
        # lock
        self.lock = Lock()
            
        # control
        self.control_raw = ''
        
        # sdk
        self.sdk = self.__get('sdk', 'meta.sdk')
        
        # prefix
        self.prefix = self.__get('prefix', 'meta.prefix')
        
        # cc
        self.cc = self.__get('cc', 'meta.cc')
        
        # c_flags
        self.c_flags = self.__get('c_flags', 'meta.cflags')
        
        # swiftc
        self.swift = self.__get('swift', 'meta.swiftc')
        
        # swift_flags
        self.swift_flags = self.__get('swift_flags', 'meta.swiftflags')
        
        # rootless
        self.rootless = self.__get('rootless', 'meta.rootless')
        
        # optimization
        self.optimization = self.__get('optimization', 'meta.optimization')
        
        # warnings
        self.warnings = self.__get('warnings', 'meta.warnings')
        
        # entitlement flag
        self.entflag = self.__get('entflag', 'meta.entflag')
        
        # entitlement file
        self.entfile = self.__get('entfile', 'meta.entfile')
                
        # compression
        self.compression = get_from_cfg(self, 'meta.compression')
        
        # archs
        self.archs = self.__get('archs', 'meta.archs')
        
        self.archs_formatted = ''

        if self.archs != None:
            for arch in self.archs: self.archs_formatted += f' -arch {arch}'
            
        # platform
        self.platform = self.__get('platform', 'meta.platform')
        
        # min version
        self.min_vers = self.__get('min_vers', 'meta.minVers')
            
        # storage dir
        self.storage = get_luz_storage()
        
        # modules
        self.modules = get_from_luzbuild(self, 'modules')
        
        # swift
        self.compile_for_swift = '.swift' in str(self.modules)

        # submodules
        self.submodules = []
        
        # ensure prefix exists
        if self.prefix is not '':
            self.prefix = resolve_path(self.prefix)
            if not self.prefix.exists():
                error('Specified prefix does not exist.')
                exit(1)
        
        # get git
        self.git = cmd_in_path('git')
        if self.git is None:
            error('Git is needed in order to use Luz.')
            exit(1)
        
        # format cc with prefix
        if self.prefix is not '' and not resolve_path(self.cc).is_relative_to('/'):
            prefix_path = cmd_in_path(f'{self.prefix}/{self.cc}')
            if not prefix_path:
                error(
                    f'C compiler "{self.cc}" not in prefix path.')
                exit(1)
            self.cc = prefix_path
        
        # format swift with prefix
        if self.prefix is not '' and not resolve_path(self.swift).is_relative_to('/'):
            prefix_path = cmd_in_path(f'{self.prefix}/{self.swift}')
            if not prefix_path:
                error(
                    f'Swift compiler "{self.swift}" not in prefix path.')
                exit(1)
            self.swift = prefix_path
        
        # format install_name_tool with prefix
        self.install_name_tool = cmd_in_path(
            f'{(str(self.prefix) + "/") if self.prefix is not None else ""}install_name_tool')
        if self.install_name_tool is None:
            # fall back to path
            self.install_name_tool = cmd_in_path('install_name_tool')
            if self.install_name_tool is None:
                error('Could not find install_name_tool.')
                exit(1)
        
        # format ldid with prefix
        self.ldid = cmd_in_path(
            f'{(str(self.prefix) + "/") if self.prefix is not None else ""}ldid')
        if self.ldid is None:
            # fall back to path
            self.ldid = cmd_in_path('ldid')
            if self.ldid is None:
                error('Could not find ldid.')
                exit(1)
        
        # format ldid with prefix
        self.strip = cmd_in_path(
            f'{(str(self.prefix) + "/") if self.prefix is not None else ""}strip')
        if self.strip is None:
            # fall back to path
            self.strip = cmd_in_path('strip')
            if self.strip is None:
                error('Could not find strip.')
                exit(1)
                
        # format lipo with prefix
        if self.compile_for_swift:
            self.lipo = cmd_in_path(
                f'{(str(self.prefix) + "/") if self.prefix is not None else ""}lipo')
            if self.lipo is None:
                # fall back to path
                self.lipo = cmd_in_path('lipo')
                if self.lipo is None:
                    error('Could not find lipo.')
                    exit(1)
            
        # attempt to manually find an sdk
        if self.sdk == '': self.sdk = self.__get_sdk()
        else:
            # ensure sdk exists
            self.sdk = resolve_path(self.sdk)
            if not self.sdk.exists():
                error(f'Specified SDK path "{self.sdk}" does not exist.')
                exit(1)
                
        # parse modules
        if self.modules is not None:
            # set compiler
            self.c_compiler = CCompiler().set_compiler(self.cc)
            # get modules
            for m in self.modules:
                # get module data
                v = self.modules.get(m)
                # make sure files is a list
                if type(v.get('files')) is not list:
                    v['files'] = [v['files']]
                # look for swift
                if '.swift' in str(v.get('files')):
                    self.compile_for_swift = True
                    if type(self.swift) is not Path:
                        self.swift = cmd_in_path(self.swift)
                        if self.swift is None:
                            error('Swift compiler not found.')
                            exit(1)
                        self.swift_compiler = SwiftCompiler().set_compiler(self.swift)
                # assign module
                self.modules[m] = assign_module(v, m, self)
        elif self.modules is None or self.modules == {}:
            if get_from_cfg(self, 'submodules') == []:
                error('No modules found in LuzBuild file.')
                exit(1)

        # parse luzbuild file
        self.pool.map(lambda x: self.__handle_key(x), self.luzbuild)

        # get submodules
        subproj_results = self.pool.map(lambda x: self.__handle_submodule(x), get_from_cfg(self, 'submodules'))
        for result in subproj_results:
            if result is not None:
                error(result)
                exit(1)

    
    def __get(self, obj_key, def_key):
        """Get a key from either the LuzBuild, inherited object, or default config."""
        if get_from_luzbuild(self, def_key) is not None:
            return get_from_luzbuild(self, def_key)
        elif self.to_inherit is not None:
            return getattr(self.to_inherit, obj_key)
        else:
            return get_from_cfg(self, def_key)
        

    def __handle_submodule(self, submodule):
        """Handle a submodule dir.
        
        :param str submodule: Directory of submodule.
        """
        path = resolve_path(submodule + '/LuzBuild')
        if not path.exists():
            return f'Submodule "{submodule}" does not exist.'
        # get luzbuild
        luzbuild = LuzBuild(clean=False, path_to_file=path, inherit=self)
        # add to submodules
        self.submodules.append(luzbuild)


    def __handle_key(self, key):
        """Handle a key in the LuzBuild file.
        
        :param str key: The key to handle.
        """
        key = str(key).lower()
        value = self.luzbuild.get(key)

        # control assignments
        if key == 'control':
            for c in value:
                v = value.get(c)
                c = str(c).lower()
                # control assignments
                if c in ['name', 'id', 'depends', 'architecture', 'version', 'maintainer', 'description', 'section', 'author', 'icon', 'priority', 'size', 'tags', 'replaces', 'provides', 'conflicts', 'installed-size', 'depiction', 'tag', 'package', 'sileodepiction']:
                    if type(v) is str:
                        end = '\n'
                        # id patch
                        if c == 'id':
                            self.control_raw += f'Package: {v}{end}'
                        # maintainer
                        elif c == 'author' and not 'maintainer' in list(self.luzbuild.get('control')):
                            self.control_raw += f'Author: {v}{end}Maintainer: {v}{end}'
                        # author
                        elif c == 'maintainer' and not 'author' in list(self.luzbuild.get('control')):
                            self.control_raw += f'Author: {v}{end}Maintainer: {v}{end}'
                        # sileodepiction patch
                        elif c == 'sileodepiction':
                            self.control_raw += f'SileoDepiction: {v}{end}'
                        # installed-size patch
                        elif c == 'installed-size':
                            self.control_raw += f'Installed-Size: {v}{end}'
                        # other values
                        else:
                            self.control_raw += f'{c.capitalize()}: {v}{end}'

    
    def __get_sdk(self):
        """Get an SDK from Xcode using xcrun."""
        xcrun = cmd_in_path('xcrun')
        if xcrun is None:
            error(
                'Xcode does not appear to be installed. Please specify an SDK manually.')
            exit(1)
        else:
            log_stdout('Finding an SDK...')
            sdkA = getoutput(
                f'{xcrun} --show-sdk-path --sdk {self.platform}').split('\n')[-1]
            if sdkA == '' or not sdkA.startswith('/'):
                error('Could not find an SDK. Please specify one manually.')
                exit(1)
            remove_log_stdout('Finding an SDK...')
            self.sdk = sdkA
        return resolve_path(self.sdk)
    
    
    def __pack(self):
        """Pack up the .deb file."""
        log_stdout('Packing deb file...', self.lock)
        # layout
        layout_path = resolve_path('layout')
        if layout_path.exists(): copytree(layout_path, f'{self.dir}/_', dirs_exist_ok=True)
        # pack
        Pack(resolve_path(f'{self.dir}/_'), algorithm=self.compression)
        remove_log_stdout('Packing deb file...', self.lock)
    
    
    def build(self):
        """Build the project."""
        # compile results
        if self.modules != None:
            if self.path != "":
                log(f'Compiling "{self.path}" for target "{self.platform}:{self.min_vers}"...')
            else:
                log(f'Compiling base project for target "{self.platform}:{self.min_vers}"...')
            compile_results = self.pool.map(lambda x: x.compile(), self.modules.values())
            for result in compile_results:
                if result is not None:
                    error(result)
                    exit(1)
        
        # compile submodules
        if self.submodules != []:
            compile_results = self.pool.map(lambda x: x.build(), self.submodules)
            for result in compile_results:
                if result is not None:
                    error(result)
                    exit(1)
        
    def build_and_pack(self):
        """Build and pack the project."""
        # build
        self.build()
        # make staging dirs
        if not resolve_path(f'{self.dir}/_/DEBIAN').exists():
            makedirs(f'{self.dir}/_/DEBIAN')
        # write control
        with open(f'{self.dir}/_/DEBIAN/control', 'w') as f:
            f.write(self.control_raw)
        self.__pack()
        log(f'Done in {round(time() - self.time, 2)} seconds.')
