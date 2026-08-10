from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from trading.scanner.models import SortDirection
from trading.scanner.query_ast import (
    BooleanNode,
    FilterNode,
    RangeNode,
    ScannerField,
    SortNode,
)
from trading.scanner.query_validator import stable_plan_hash
from trading.scanner.schemas import ScannerQueryPlan

from desktop.workspace.models import QueryPlanChange, QueryPlanModification


def _replace_price_range(
    node: FilterNode, minimum: float, maximum: float
) -> tuple[FilterNode, int]:
    if isinstance(node, RangeNode) and node.field == ScannerField.CURRENT_PRICE:
        return (
            node.model_copy(update={"minimum": minimum, "maximum": maximum}),
            1,
        )
    if isinstance(node, BooleanNode):
        children: list[FilterNode] = []
        count = 0
        for child in node.children:
            updated, child_count = _replace_price_range(child, minimum, maximum)
            children.append(updated)
            count += child_count
        return node.model_copy(update={"children": children}), count
    return node, 0


class IncrementalQueryPlanModifier:
    def modify(
        self,
        plan: ScannerQueryPlan,
        instruction: str,
    ) -> QueryPlanModification:
        text = re.sub(r"\s+", "", instruction).casefold()
        updates: dict[str, Any] = {}
        changes: list[QueryPlanChange] = []

        price_match = re.search(
            r"(?:价格|股价)?(?:改成|调整为|变成)?(\d+(?:\.\d+)?)"
            r"(?:到|至|-)(\d+(?:\.\d+)?)元",
            text,
        )
        if price_match:
            minimum, maximum = map(float, price_match.groups())
            new_filters: list[FilterNode] = []
            replaced = 0
            for node in plan.filters:
                updated, count = _replace_price_range(node, minimum, maximum)
                new_filters.append(updated)
                replaced += count
            if replaced != 1:
                return QueryPlanModification(
                    plan=None,
                    changes=[],
                    clarification_required=True,
                    clarification_question="无法唯一确定要修改的价格区间，请明确字段和范围。",
                )
            updates["filters"] = new_filters
            changes.append(
                QueryPlanChange(
                    field="filters.current_price_range",
                    before=[
                        item.model_dump(mode="json") for item in plan.filters
                    ],
                    after=[item.model_dump(mode="json") for item in new_filters],
                )
            )

        if "排除创业板" in text:
            before = list(plan.exclude_boards)
            after = list(dict.fromkeys([*before, "CHINEXT"]))
            updates["exclude_boards"] = after
            changes.append(
                QueryPlanChange(field="exclude_boards", before=before, after=after)
            )

        industry_match = re.search(r"只看([^,，。；;]{2,20})", text)
        if industry_match:
            industry = industry_match.group(1)
            before = list(plan.include_industries)
            after = [industry]
            updates["include_industries"] = after
            changes.append(
                QueryPlanChange(
                    field="include_industries", before=before, after=after
                )
            )

        top_match = re.search(r"(?:前\d+只)?改成前(10|20|30)只", text)
        if top_match:
            after_top = int(top_match.group(1))
            updates["top_n"] = after_top
            changes.append(
                QueryPlanChange(
                    field="top_n", before=plan.top_n, after=after_top
                )
            )

        sort_map = {
            "按量比排序": ScannerField.VOLUME_RATIO_5D,
            "按成交额排序": ScannerField.AMOUNT,
            "按涨幅排序": ScannerField.CHANGE_PCT,
            "按换手率排序": ScannerField.TURNOVER_RATE,
        }
        for phrase, field in sort_map.items():
            if phrase not in text:
                continue
            before_sort = [
                item.model_dump(mode="json") for item in plan.sort_fields
            ]
            after_sort = [SortNode(field=field, direction=SortDirection.DESC)]
            updates["sort_fields"] = after_sort
            changes.append(
                QueryPlanChange(
                    field="sort_fields",
                    before=before_sort,
                    after=[
                        item.model_dump(mode="json") for item in after_sort
                    ],
                )
            )
            break

        if not changes:
            return QueryPlanModification(
                plan=None,
                changes=[],
                clarification_required=True,
                clarification_question="未识别要修改的既有筛选条件，请明确字段和新值。",
            )
        values = plan.model_dump()
        values.update(updates)
        values.update(
            {
                "original_query": f"{plan.original_query}；{instruction}",
                "normalized_query": f"{plan.normalized_query}；{text}",
                "generated_at": datetime.now().astimezone(),
            }
        )
        values["plan_hash"] = stable_plan_hash(values)
        values["query_id"] = "sq_" + values["plan_hash"][:24]
        updated_plan = ScannerQueryPlan.model_validate(values)
        return QueryPlanModification(
            plan=updated_plan.model_dump(mode="json"),
            changes=changes,
        )


__all__ = ["IncrementalQueryPlanModifier"]
