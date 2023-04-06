import yaml


# Retrieve values
def get_credentials(key: str | list, path: str = './development/config.yaml') -> str | list[str]:
    """
    This function retrieves data from a yaml file.

    Args:
        key (str | list): Key/keys for the requested value/values.
        path (str): Path to the config file.

    Returns:
        value (str | list[str]): Value/values to the given key/keys.
    """
    if type(key) == list:
        with open(path, 'r') as file:
            yaml_data = yaml.safe_load(file)
            result = []
            for k in key:
                result.append(yaml_data[k])
            return result
    else:
        with open(path, 'r') as cfg:
            return yaml.safe_load(cfg)[key]
