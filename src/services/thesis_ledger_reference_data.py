"""补充参考数据的请求分派，复用当前数据路由及主备执行。"""

from .thesis_ledger_security_static_query import static_query_operation
from .thesis_ledger_security_lot_query import lot_query_operation
from .thesis_ledger_status_query_operation import status_query_operation
from .thesis_ledger_public_fees_query import fees_query_operation
from .thesis_ledger_legacy_status_query import legacy_status_operation


def reference_data_operation(request, error_type, runtime=None):
    legacy_operation = legacy_status_operation(request, error_type)
    if legacy_operation is not None:
        return legacy_operation
    fees_operation = fees_query_operation(request, error_type)
    if fees_operation is not None:
        return fees_operation
    status_operation = status_query_operation(request, error_type, runtime)
    if status_operation is not None:
        return status_operation
    static_operation = static_query_operation(request, error_type)
    if static_operation is not None:
        return static_operation
    return lot_query_operation(request, error_type)
