# Divide a list
def divide_list(given_list: list, chunk_size: int):
    """
    This function divides a given list into multiple smaller lists.

    Args:
        given_list (list): List to be divided.
        chunk_size (int): How long the chunks should be.
    """
    for i in range(0, len(given_list), chunk_size):
        yield given_list[i:i + chunk_size]
