"""已验CD2/CLOUDAPI精确构建组合；不按主版本或分别命中放行。"""

SUPPORTED_RUNTIME = ('1.1.2 Build 26-10-06 23:48:14', '1.1.2 Build 26-10-06 23:48:12')
SUPPORTED_RUNTIMES = frozenset({
    SUPPORTED_RUNTIME,
    ('1.1.3 Build 26-10-17 17:13:11', '1.1.3 Build 26-10-17 17:13:00'),
})
