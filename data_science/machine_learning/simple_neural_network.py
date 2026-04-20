import numpy as np


# The sigmoid function, which is used as the activation function for the network
def sigmoid(x):
    return 1 / (1 + np.exp(-x))


# The derivative of the sigmoid function
def sigmoid_derivative(x):
    return x * (1 - x)


# The neural network's weights
weights = np.array([[0.50], [0.50], [0.50]])

# The learning rate
learning_rate = 0.5

# Training inputs, with a bias term of 1 added
inputs = np.array([[0, 0, 1],
                   [0, 1, 1],
                   [1, 0, 1],
                   [1, 1, 1]])

# Training outputs
outputs = np.array([[0], [1], [1], [0]])

# Train the neural network
for _ in range(10000):
    input_layer = inputs
    outputs_pred = sigmoid(np.dot(input_layer, weights))

    # how much did we miss?
    error = outputs - outputs_pred

    # multiply how much we missed by the
    # slope of the sigmoid at the values in outputs_pred
    adjustments = error * sigmoid_derivative(outputs_pred)

    # update weights
    weights += np.dot(input_layer.T, adjustments)

print("Weights after training")
print(weights)

print("Output after training")
print(outputs_pred)
