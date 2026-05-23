import json

from django.http import HttpResponse
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse


SITE_NAME = "BRIMOON Studio"
SITE_DOMAIN = "https://anna.listoya.es"

SERVICES = [
    {
        "slug": "cejas-definicion-depilacion-lifting",
        "title": "Definición, depilación y lifting de cejas",
        "short": "Diseño de cejas, depilación precisa y lifting para realzar tu mirada.",
        "image": "Definición, depilación y lifting de cejas.png",
        "meta": "Diseño de cejas, depilación y lifting en BRIMOON Studio: cuidado profesional para realzar la expresión natural de tu mirada.",
        "intro": "Un servicio pensado para ordenar, equilibrar y elevar la expresión del rostro sin perder naturalidad.",
        "benefits": [
            "Diseño adaptado a la forma del rostro y a la densidad natural de la ceja.",
            "Depilación precisa para limpiar el contorno sin endurecer la mirada.",
            "Lifting para aportar dirección, volumen visual y un acabado más pulido.",
        ],
        "tips": [
            "Evita retocar la ceja en casa antes de la cita para poder trabajar con más margen.",
            "No apliques aceites ni productos muy grasos el mismo día del lifting.",
            "Respeta las primeras horas de cuidado posterior para mantener el resultado más tiempo.",
        ],
    },
    {
        "slug": "depilacion-facial",
        "title": "Depilación facial",
        "short": "Tratamientos delicados para una piel más limpia, suave y cuidada.",
        "image": "Depilación facial.png",
        "meta": "Depilación facial delicada en BRIMOON Studio: cuidado de la piel, precisión y acabado suave con cita previa.",
        "intro": "Trabajamos la depilación facial con precisión y cuidado para conseguir una piel más limpia sin perder confort.",
        "benefits": [
            "Acabado limpio en zonas visibles del rostro.",
            "Técnica delicada para cuidar pieles sensibles siempre que sea posible.",
            "Resultado ordenado que mejora la sensación de suavidad y frescura.",
        ],
        "tips": [
            "No exfolies la zona el día anterior si tu piel suele reaccionar.",
            "Evita sol directo y calor intenso justo después del servicio.",
            "Mantén la hidratación suave y evita activos fuertes durante las primeras horas.",
        ],
    },
    {
        "slug": "manicura-extensiones-tratamientos",
        "title": "Manicura, extensiones y tratamientos",
        "short": "Manicuras elegantes, extensiones y tratamientos para unas manos impecables.",
        "image": "Manicura, extensiones y tratamientos.png",
        "meta": "Manicura, extensiones y tratamientos de uñas en BRIMOON Studio: acabado elegante, cuidado de manos y diseño personalizado.",
        "intro": "La manicura en Brimoon combina estética, preparación cuidada y detalles que elevan el acabado final.",
        "benefits": [
            "Preparación de la uña y cutícula para un acabado más limpio.",
            "Extensiones y tratamientos orientados a mejorar forma, resistencia y estilo.",
            "Diseños elegantes que se adaptan a tu día a día o a una ocasión especial.",
        ],
        "tips": [
            "Hidrata las manos a diario, pero evita crema justo antes de la cita.",
            "Usa aceite de cutícula para mantener un aspecto más cuidado entre visitas.",
            "No arranques producto si notas levantamiento: agenda una revisión para proteger la uña natural.",
        ],
    },
    {
        "slug": "pedicuras-tratamientos-pies",
        "title": "Pedicuras y tratamientos",
        "short": "Cuidado completo de pies, pedicura estética y tratamientos para bienestar y belleza.",
        "image": "Pedicuras y tratamientos Pies.png",
        "meta": "Pedicuras y tratamientos de pies en BRIMOON Studio: cuidado estético, bienestar y acabado elegante con cita previa.",
        "intro": "Un cuidado completo para que los pies se vean más bonitos y se sientan más descansados.",
        "benefits": [
            "Cuidado estético de uñas y piel para un aspecto más pulido.",
            "Tratamientos orientados al bienestar y a la sensación de ligereza.",
            "Acabados limpios para sandalias, eventos o mantenimiento regular.",
        ],
        "tips": [
            "Llega con calzado cómodo si vas a elegir esmaltado tradicional.",
            "Hidrata los pies por la noche para mantener la piel más flexible.",
            "Agenda mantenimiento regular si buscas un resultado cuidado todo el año.",
        ],
    },
    {
        "slug": "pestanas-tinte-extensiones-lifting",
        "title": "Tinte, extensiones y lifting de pestañas",
        "short": "Color, volumen y curvatura para una mirada más intensa y definida.",
        "image": "Tinte, extensiones y lifting de pestañas..png",
        "meta": "Tinte, extensiones y lifting de pestañas en BRIMOON Studio: mirada más definida, volumen y curvatura con acabado premium.",
        "intro": "Servicios para intensificar la mirada con un resultado elegante, definido y adaptado a tus facciones.",
        "benefits": [
            "Tinte para aportar profundidad visual a pestañas claras o poco marcadas.",
            "Lifting para elevar la curvatura natural sin efecto pesado.",
            "Extensiones para conseguir más volumen y presencia según el estilo buscado.",
        ],
        "tips": [
            "Acude sin máscara de pestañas para que el trabajo sea más limpio.",
            "Evita vapor, aceites y agua directa durante las primeras horas si se indica en cabina.",
            "Cepilla las pestañas con suavidad para mantenerlas ordenadas entre visitas.",
        ],
    },
]

ARTICLES = [
    {
        "slug": "como-cuidar-cejas-despues-lifting",
        "title": "Cómo cuidar tus cejas después de un lifting",
        "category": "Cejas",
        "summary": "Pequeños hábitos para conservar la forma, el brillo y la dirección del lifting de cejas.",
        "meta": "Consejos para cuidar las cejas después de un lifting: primeras horas, hidratación y mantenimiento profesional.",
        "image": "Definición, depilación y lifting de cejas.png",
        "sections": [
            ("Las primeras horas importan", "El resultado se estabiliza mejor cuando se evita humedad intensa, vapor y productos grasos durante el tiempo recomendado en cabina."),
            ("Peina con suavidad", "Un cepillado ligero ayuda a mantener la dirección sin forzar el pelo. La clave es ordenar, no arrastrar."),
            ("Mantenimiento inteligente", "Agenda retoques cuando notes que la dirección empieza a perder fuerza. Forzar la ceja en casa suele empeorar el acabado."),
        ],
    },
    {
        "slug": "manicura-elegante-que-dura",
        "title": "Manicura elegante: hábitos para que dure más",
        "category": "Manicura",
        "summary": "Cuidado diario, aceite de cutícula y gestos sencillos para proteger el acabado.",
        "meta": "Consejos de manicura elegante y duradera: cuidado de manos, cutículas y mantenimiento entre citas.",
        "image": "Manicura, extensiones y tratamientos.png",
        "sections": [
            ("Protege el acabado", "Usa guantes para tareas con agua o productos de limpieza. Es el gesto más simple y uno de los más eficaces."),
            ("Cuida la cutícula", "El aceite aplicado con constancia mejora el aspecto de la manicura y ayuda a que el contorno se vea más limpio."),
            ("No retires producto en casa", "Si aparece levantamiento, lo mejor es revisar la uña en cabina para evitar dañar la base natural."),
        ],
    },
    {
        "slug": "depilacion-facial-piel-sensible",
        "title": "Depilación facial y piel sensible: qué tener en cuenta",
        "category": "Piel",
        "summary": "Preparación y cuidados posteriores para que la piel se sienta más calmada.",
        "meta": "Depilación facial para piel sensible: recomendaciones antes y después para cuidar la piel.",
        "image": "Depilación facial.png",
        "sections": [
            ("Prepara la piel", "Evita exfoliaciones fuertes antes de la cita si sueles tener rojez o sensibilidad."),
            ("Menos es más", "Después del servicio, elige hidratación suave y evita activos intensos durante las primeras horas."),
            ("Observa tu ritmo", "Cada piel responde diferente. Comentar tus reacciones anteriores ayuda a ajustar mejor el servicio."),
        ],
    },
    {
        "slug": "mirada-natural-pestanas-cejas",
        "title": "Mirada natural: combinar cejas y pestañas sin exceso",
        "category": "Mirada",
        "summary": "Ideas para realzar la mirada manteniendo un acabado fino y sofisticado.",
        "meta": "Cómo combinar cejas y pestañas para una mirada natural, elegante y definida en BRIMOON Studio.",
        "image": "Tinte, extensiones y lifting de pestañas..png",
        "sections": [
            ("Equilibrio antes que intensidad", "Una mirada elegante no siempre necesita más volumen. A veces basta con dirección, color y proporción."),
            ("Respeta tus facciones", "El diseño debe acompañar la forma natural del ojo y del rostro para que el resultado se vea propio."),
            ("Planifica por eventos", "Si tienes una ocasión especial, reserva con margen para ajustar el resultado sin prisas."),
        ],
    },
]


def _absolute_url(request, path):
    return f"{SITE_DOMAIN}{path}"


def _find_by_slug(items, slug):
    for item in items:
        if item["slug"] == slug:
            return item
    raise Http404("Página no encontrada")


def _service_schema(request, service):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Service",
        "name": service["title"],
        "description": service["meta"],
        "provider": {"@type": "BeautySalon", "name": SITE_NAME},
        "areaServed": "Bilbao",
        "url": _absolute_url(request, reverse("service_detail", args=[service["slug"]])),
    }, ensure_ascii=False)


def _article_schema(request, article):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["meta"],
        "author": {"@type": "Organization", "name": SITE_NAME},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
        "mainEntityOfPage": _absolute_url(request, reverse("article_detail", args=[article["slug"]])),
    }, ensure_ascii=False)


def home(request):
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "BeautySalon",
        "name": SITE_NAME,
        "url": _absolute_url(request, reverse("home")),
        "description": "Salón de belleza premium especializado en cejas, pestañas, manicura, pedicura y depilación facial.",
        "address": {"@type": "PostalAddress", "addressLocality": "Bilbao", "addressCountry": "ES"},
        "openingHours": "Mo-Sa by appointment",
        "sameAs": [],
        "makesOffer": [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": service["title"]}} for service in SERVICES],
    }, ensure_ascii=False)
    return render(request, "core/home.html", {
        "services": SERVICES,
        "articles": ARTICLES[:3],
        "canonical_url": _absolute_url(request, reverse("home")),
        "schema_json": schema,
    })


def service_index(request):
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Servicios de belleza en BRIMOON Studio",
        "description": "Servicios de cejas, pestañas, manicura, pedicura y depilación facial.",
        "url": _absolute_url(request, reverse("service_index")),
    }, ensure_ascii=False)
    return render(request, "core/service_index.html", {
        "services": SERVICES,
        "articles": ARTICLES[:3],
        "canonical_url": _absolute_url(request, reverse("service_index")),
        "schema_json": schema,
        "meta_description": "Servicios de belleza premium en BRIMOON Studio: cejas, pestañas, manicura, pedicura y depilación facial con cita previa.",
    })


def service_detail(request, slug):
    service = _find_by_slug(SERVICES, slug)
    related = [item for item in SERVICES if item["slug"] != slug][:3]
    return render(request, "core/service_detail.html", {
        "service": service,
        "related_services": related,
        "articles": ARTICLES[:3],
        "canonical_url": _absolute_url(request, reverse("service_detail", args=[slug])),
        "schema_json": _service_schema(request, service),
    })


def advice_index(request):
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": "Consejos de belleza BRIMOON Studio",
        "description": "Consejos, ideas y cuidados para cejas, pestañas, manicura, pedicura y depilación facial.",
        "url": _absolute_url(request, reverse("advice_index")),
    }, ensure_ascii=False)
    return render(request, "core/advice_index.html", {
        "articles": ARTICLES,
        "services": SERVICES,
        "canonical_url": _absolute_url(request, reverse("advice_index")),
        "schema_json": schema,
        "meta_description": "Consejos de belleza, cuidados y trucos profesionales de BRIMOON Studio para cejas, pestañas, manicura y piel.",
    })


def article_detail(request, slug):
    article = _find_by_slug(ARTICLES, slug)
    related_articles = [item for item in ARTICLES if item["slug"] != slug][:3]
    return render(request, "core/article_detail.html", {
        "article": article,
        "related_articles": related_articles,
        "services": SERVICES[:3],
        "canonical_url": _absolute_url(request, reverse("article_detail", args=[slug])),
        "schema_json": _article_schema(request, article),
    })


def robots_txt(request):
    content = f"""User-agent: *
Allow: /
Disallow: /panel/
Disallow: /dj-admin/
Disallow: /api/
Sitemap: {SITE_DOMAIN}{reverse('sitemap_xml')}
"""
    return HttpResponse(content, content_type="text/plain")


def sitemap_xml(request):
    paths = [
        reverse("home"),
        reverse("service_index"),
        reverse("advice_index"),
    ]
    paths += [reverse("service_detail", args=[service["slug"]]) for service in SERVICES]
    paths += [reverse("article_detail", args=[article["slug"]]) for article in ARTICLES]
    urls = "\n".join(
        f"  <url><loc>{_absolute_url(request, path)}</loc><changefreq>weekly</changefreq><priority>{'1.0' if path == reverse('home') else '0.8'}</priority></url>"
        for path in paths
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""
    return HttpResponse(xml, content_type="application/xml")
