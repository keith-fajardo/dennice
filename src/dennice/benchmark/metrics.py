from dennice.benchmark.schema import GoldCognitiveDemands, RoutingMetrics
from dennice.core.models import RoutingDecision


def evaluate_routing(
    predicted: RoutingDecision | None,
    gold: GoldCognitiveDemands,
) -> RoutingMetrics:
    """Evaluate only explicitly annotated cognitive labels; task family is not inferred."""
    if predicted is None:
        return RoutingMetrics()
    predicted_demands = {score.demand for score in predicted.cognitive_demands}
    gold_demands = gold.all_demands
    intersection = predicted_demands & gold_demands
    precision = len(intersection) / len(predicted_demands) if predicted_demands else 0.0
    recall = len(intersection) / len(gold_demands) if gold_demands else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return RoutingMetrics(
        primary_correct=predicted.primary_demand == gold.primary,
        multilabel_precision=precision,
        multilabel_recall=recall,
        multilabel_f1=f1,
    )
