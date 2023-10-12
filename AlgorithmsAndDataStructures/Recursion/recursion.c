#include <stdio.h>

int recursive_sum(int numbers[], int length) {
    if (length <= 3) {
        int sum = 0;
        for (int i = 0; i < length; i++) {
            sum += numbers[i];
        }
        return sum;
    } else {
        int sums_length = (length + 2) / 3;
        int sums[sums_length];
        for (int i = 0; i < length; i += 3) {
            int subarray_length = (i + 3 <= length) ? 3 : length - i;
            sums[i / 3] = recursive_sum(&numbers[i], subarray_length);
        }
        return recursive_sum(sums, sums_length);
    }
}

int main() {
    int numbers[] = {1, 2, 3, 4, 5, 6, 7, 8, 9};
    int result = recursive_sum(numbers, sizeof(numbers) / sizeof(numbers[0]));
    printf("%d\n", result);  // Output: 45
    return 0;
}
