def divide_by_user_input(value, denominator=1):
    # Validate denominator to avoid ZeroDivisionError.
    if denominator == 0:
        raise ValueError("denominator must be non-zero")
    result = value / denominator
    return result

# Calling the function will cause a crash during execution
if __name__ == "__main__":
    print(divide_by_user_input(10))