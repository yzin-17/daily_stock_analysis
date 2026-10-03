"""BaoStock 批量名称响应的身份校验，不推断历史证券状态。"""

import re


def validated_names(fields, rows):
    """验证整批响应后返回名称映射；冲突或坏行不会产出部分结果。"""
    if len(fields) != len(set(fields)) or not {'code', 'code_name'}.issubset(fields):
        raise ValueError('名称响应缺少唯一字段')
    code_index = fields.index('code')
    name_index = fields.index('code_name')
    identities = {}
    names = {}
    for row in rows:
        if len(row) != len(fields):
            raise ValueError('名称响应字段数不匹配')
        source_code, source_name = row[code_index], row[name_index]
        if not isinstance(source_code, str) or not re.fullmatch(r'(sh|sz|bj)\.[0-9]{6}', source_code):
            raise ValueError('名称响应代码无效')
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError('名称响应名称无效')
        code = source_code.split('.')[1]
        name = source_name.strip()
        if code in identities and (identities[code] != source_code or names[code] != name):
            raise ValueError('名称响应身份冲突')
        identities[code] = source_code
        names[code] = name
    return names
