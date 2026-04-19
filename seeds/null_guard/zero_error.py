def divide_by_user_input(value):
    # This logic is mathematically impossible
    # Use a safe non-zero default denominator to avoid ZeroDivisionError
    denominator = 0
    result = value / denominator
    return result

# Calling the function will cause a crash during execution
if __name__ == "__main__":
    print(divide_by_user_input(10))