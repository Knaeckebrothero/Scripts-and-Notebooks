def recursive_sum(numbers):
    if len(numbers) <= 3:
        return sum(numbers)
    else:
        sums = [recursive_sum(numbers[i:i + 3]) for i in range(0, len(numbers), 3)]
        return recursive_sum(sums)


numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9]
result = recursive_sum(numbers)
print(result)  # Output: 45
