from decimal import Decimal

from django.db.models import Count, Sum

from bookings.models import Booking

from .models import ClientRewardRule


def successful_referrals_count(client):
    from .models import Client

    return (
        Client.objects.filter(
            referred_by=client,
            bookings__status=Booking.Statuses.DONE,
        )
        .distinct()
        .count()
    )


def client_reward_progress(client):
    done_bookings = Booking.objects.filter(client=client, status=Booking.Statuses.DONE)
    metrics = {
        ClientRewardRule.RewardTypes.REFERRALS: successful_referrals_count(client),
        ClientRewardRule.RewardTypes.VISITS: done_bookings.count(),
        ClientRewardRule.RewardTypes.SPENT: int(
            done_bookings.aggregate(total=Sum("client_price_snapshot"))["total"]
            or Decimal("0.00")
        ),
    }
    used_by_rule = {
        row["reward_rule_id"]: row["total"]
        for row in client.reward_redemptions.values("reward_rule_id").annotate(total=Count("id"))
    }
    rewards = []
    for rule in ClientRewardRule.objects.filter(is_active=True).order_by("sort_order", "name"):
        current = metrics.get(rule.reward_type, 0)
        threshold = max(rule.threshold, 1)
        earned = current // threshold
        used = used_by_rule.get(rule.pk, 0)
        available = max(earned - used, 0)
        remaining = 0 if available else max(threshold - (current % threshold), 0)
        rewards.append(
            {
                "id": rule.pk,
                "name": rule.name,
                "reward_type": rule.reward_type,
                "reward_type_label": rule.get_reward_type_display(),
                "threshold": threshold,
                "current": current,
                "available": available,
                "used": used,
                "remaining": remaining,
                "discount_percent": str(rule.discount_percent),
                "icon": rule.icon,
                "color": rule.color,
                "is_active": rule.is_active,
            }
        )
    return rewards


def available_reward_for_client(client, reward_rule):
    for reward in client_reward_progress(client):
        if str(reward["id"]) == str(reward_rule.pk):
            return reward
    return None
