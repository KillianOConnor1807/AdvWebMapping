from decimal import Decimal

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand

from gyms.models import Gym

DATA_SOURCE = "Manual research, Oct 2026"

# price is the monthly price in euro (None = not listed); see `classes` for other pricing
GYMS = [
    {
        "name": "Warriors Thai Boxing", "lat": 53.288464325583185, "lng": -6.153140852154523,
        "price": Decimal("90"), "level": "mixed", "beginner_friendly": True,
        "address": "3A Monkstown Farm, Dún Laoghaire, Dublin", "eircode": "A96 Y6P6", "website": "",
        "classes": "€90/month intermediate, €120/month advanced. Beginners course available.",
    },
    {
        "name": "Wai Tribe", "lat": 53.24701077455975, "lng": -6.127138760156763,
        "price": Decimal("85"), "level": "mixed", "beginner_friendly": True,
        "address": "Loughlinstown Dr, Loughlinstown, Dublin", "eircode": "A96 X0D1", "website": "",
    },
    {
        "name": "Jai Sua", "lat": 53.18736501585655, "lng": -6.106633427622913,
        "price": None, "level": "mixed", "beginner_friendly": True,
        "address": "3 Pinewood Cl, Boghall Rd, Oldcourt, Bray, Co. Wicklow", "eircode": "W98 EK76",
        "website": "https://jaisuamt.com", "classes": "€10 per class (pay as you go).",
    },
    {
        "name": "Origins Muay Thai", "lat": 53.35079999803016, "lng": -6.283445486984288,
        "price": Decimal("60"), "level": "competition", "beginner_friendly": False,
        "address": "11 Arbour Pl, Stoneybatter, Dublin", "eircode": "D07 R206",
        "website": "https://www.originsmuaythai.com",
    },
    {
        "name": "Hammerhead Muay Thai", "lat": 53.3569384017656, "lng": -6.240491687135618,
        "price": Decimal("100"), "level": "beginner", "beginner_friendly": True,
        "address": "Rear of 39 Strandville Ave, North Strand Rd, North Wall, Dublin 3", "eircode": "D03 AW80",
        "website": "https://hammerheadmuaythai.com",
    },
    {
        "name": "Bextreme Muay Thai", "lat": 53.35372349811746, "lng": -6.227985218633309,
        "price": Decimal("100"), "level": "beginner", "beginner_friendly": True,
        "address": "Unit 13B, East Wall Rd, North Wall, Dublin 3", "eircode": "D03 E440",
        "website": "https://www.bextremefightclub.com",
    },
    {
        "name": "Dublin Combat Academy", "lat": 53.404619928474474, "lng": -6.236886979895258,
        "price": Decimal("100"), "level": "competition", "beginner_friendly": True,
        "address": "Santry, Dublin 9 (Plus Code CQ36+JWM)", "eircode": "", "website": "",
    },
    {
        "name": "SBG HQ", "lat": 53.33679685659306, "lng": -6.322617110420445,
        "price": None, "level": "mixed", "beginner_friendly": True,
        "address": "Unit 13, Goldenbridge Industrial Estate, Inchicore, Dublin", "eircode": "D08 WK22",
        "website": "https://www.sbgireland.com/muay-thai/",
    },
    {
        "name": "309 Phuang Malai Muay Thai", "lat": 53.34455316480548, "lng": -6.248484702479595,
        "price": Decimal("100"), "level": "competition", "beginner_friendly": True,
        "address": "14 Magennis Pl, Dublin", "eircode": "D02 HR70",
        "website": "https://www.309phuangmalai.com",
    },
    {
        "name": "Holohan Martial Arts", "lat": 53.299081500775, "lng": -6.34692192426802,
        "price": None, "level": "beginner", "beginner_friendly": True,
        "address": "Unit GB1, Sitecast Industrial Estate, Greenhills Rd, Tallaght, Dublin 24", "eircode": "",
        "website": "https://holohanmartialarts.ie",
    },
    {
        "name": "Vieira Martial Arts", "lat": 53.349193435464706, "lng": -6.261416261880408,
        "price": Decimal("99"), "level": "mixed", "beginner_friendly": True,
        "address": "40 North Lotts, North City, Dublin", "eircode": "D01 C8C9",
        "website": "https://www.vieiramartialarts.com",
    },
    {
        "name": "Dublin Thai Boxing", "lat": 53.39802033189393, "lng": -6.1868480084741,
        "price": None, "level": "mixed", "beginner_friendly": True,
        "address": "Kilbarrack Parade, Priorswood, Dublin", "eircode": "", "website": "",
    },
]


class Command(BaseCommand):
    help = "Populate the database with Dublin Muay Thai gyms"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete existing gyms first")

    def handle(self, *args, clear=False, **options):
        if clear:
            deleted, _ = Gym.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} existing gyms")

        created = 0
        for gym in GYMS:
            _, was_created = Gym.objects.update_or_create(
                name=gym["name"],
                defaults={
                    "location": Point(gym["lng"], gym["lat"], srid=4326),  # x=lng, y=lat
                    "price_per_month": gym["price"],
                    "level": gym["level"],
                    "beginner_friendly": gym["beginner_friendly"],
                    "address": gym["address"],
                    "eircode": gym["eircode"],
                    "website": gym["website"],
                    "classes": gym.get("classes", ""),
                    "data_source": DATA_SOURCE,
                },
            )
            created += was_created

        self.stdout.write(self.style.SUCCESS(
            f"Done: {created} created, {len(GYMS) - created} updated, {Gym.objects.count()} total"))
