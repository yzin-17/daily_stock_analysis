"""静态来源工厂的标准失败边界；不修改凭据、缓存或SDK异常。"""

from typing import Callable, Protocol

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext


class FactoryErrorConstructor(Protocol):
    def __call__(self, code: str, message: str, *, retryable: bool) -> Exception: ...


def execute_source_factory(factory: Callable[[ReadContext], object],
                           error_type: FactoryErrorConstructor, context: ReadContext) -> object:
    try:
        return factory(context)
    except CapabilityContractError as error:
        # 工厂原文/诊断可能包含凭据；只保留标准类别与重试语义。
        raise error_type(error.code, 'Provider 适配器未就绪', retryable=error.retryable) from None
