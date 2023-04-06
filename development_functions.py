import yaml


# Retrieve credentials from a yaml file.
def read_config(key: str) -> str:
    with open('.development.yaml', 'r') as cfg:
        return yaml.safe_load(cfg)[key]


# Retrieve a list from a yaml file.
def get_player_data(keys: list[str]) -> tuple:
    with open('./development/data.yaml', 'r') as file:
        yaml_data = yaml.safe_load(file)
        result = tuple()
        for k in keys:
            result = result + (yaml_data[k], )
    return result
