import yaml


class PathBuilder:
    def __init__(self, tenant_id: str, environment: str, config_file: str):
        self.tenant_id = tenant_id
        self.environment = environment
        self.config = PathBuilder._load_config(config_file)

    @staticmethod
    def _load_config(config_file: str) -> dict:
        with open(config_file, 'r') as file:
            return yaml.safe_load(file)

    @staticmethod
    def _param_global_base(*keys: str) -> str:
        return '/' + '/'.join([key for key in list(keys) if key])

    def _param_base(self, *keys: str) -> str:
        return '/' + '/'.join(
            [key for key in [self.tenant_id] + list(keys) if key])

    def _param_env_base(self, *keys: str) -> str:
        return self._param_base(self.environment, *keys)

    def get_ssm_path(self, *keys: str, global_param: bool = False) -> str:
        try:
            path = self.config
            for key in keys:
                path = path[key]
            if isinstance(path, dict):
                raise ValueError(
                    f"Invalid parameter path: {'/'.join(keys)}, "
                    "expected a string but found a dictionary")
            if global_param:
                return PathBuilder._param_global_base(path)
            return self._param_env_base(path)
        except KeyError as e:
            raise ValueError(
                f"Invalid parameter path: {'/'.join(keys)}") from e

    def get_ssm_path_all(self) -> str:
        return self._param_env_base("*")
