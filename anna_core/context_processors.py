from django.conf import settings


def salon_globals(request):
    demo = getattr(settings, "DEMO_MODE", False)
    return {
        "salon_name": getattr(settings, "SALON_NAME", "BRIMOON Studio"),
        "demo_mode": demo,
        "salon_logo": "demo/logo-aura-studio.jpg" if demo else "logo.png",
        "salon_hero": "demo/manicure-2.jpg" if demo else "Fon2.png",
    }
