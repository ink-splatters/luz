# local imports
from ...common.utils import resolve_path

class Script:
    def __init__(self, script_type: str, path: str = None, content: str = None):
        """Add a maintainer script to the package.
        
        :param str script_type: Type of script.
        :param str path: Path to script.
        :param str content: Content of script.
        """

        # type
        self.type = script_type

        # path
        if path is not None:
            self.path = resolve_path(path)
            if not self.path.exists():
                raise FileNotFoundError(f"Script path {self.path} does not exist.")
            else:
                self.content = self.path.read_text()
        elif content is not None:
            self.content = content
        else:
            raise Exception(f"Either path or content must be set for script of type {script_type}.")
