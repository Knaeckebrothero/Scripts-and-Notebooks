"""
This module contains functions used for loading test data.
"""

import yaml


# Retrieve values
def credentials(key: str | list, path: str = './development/config.yaml') -> str | list[str]:
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


# Retrieve a list from a yaml file.
def data(keys: list[str],  path: str = './development/data.yaml') -> tuple:
    """
    This function retrieves testdata from a yaml file.
    It takes a list of keys and returns a tuple of values.
    This is meant to be a universal approach,
    that can either retrieve one or multiple values based on the number of keys.

    Args:
        keys (list[str]): Key/keys for the requested value/values.
        path (str): Path to the data file.

    Returns:
        value (tuple): Value/values to the given key/keys.
    """
    with open(path, 'r') as file:
        yaml_data = yaml.safe_load(file)
        result = tuple()
        for k in keys:
            result = result + (yaml_data[k],)
    return result
