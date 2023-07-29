# Tic Tac Toe game in Python

def print_board(board):
    for line in board:
        print(line)


def check_win(board):
    for i in range(3):
        if all(el == board[i][0] for el in board[i]) and board[i][0] != -1:  # check row
            return True
        if all(board[el][i] == board[0][i] for el in range(3)) and board[0][i] != -1:  # check column
            return True
    if board[0][0] == board[1][1] == board[2][2] and board[0][0] != -1:  # check main diagonal
        return True
    if board[0][2] == board[1][1] == board[2][0] and board[0][2] != -1:  # check other diagonal
        return True
    return False


def play_game():
    board = [[-1, -1, -1] for _ in range(3)]  # -1 represents an empty space
    current_player = 0

    while True:
        print(f"Player {current_player}'s turn. Enter row and column (0-2) separated by a space:")
        row, col = map(int, input().split())
        if row not in range(3) or col not in range(3) or board[row][col] != -1:
            print("Invalid move. Try again.")
            continue

        board[row][col] = current_player
        print_board(board)

        if check_win(board):
            print(f"Player {current_player} wins!")
            break

        if all(el != -1 for row in board for el in row):  # if no empty spaces are left
            print("It's a draw!")
            break

        current_player = 1 - current_player  # switch player


play_game()
