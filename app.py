def intersection(nums, k):
    output = 0
    nums_dict = {}

    for num in nums:
        if num in nums_dict.keys():
            nums_dict.add(num)

    

    return nums_dict

nums = [1,2,2,1]
k = 1
print(intersection(nums, k))
