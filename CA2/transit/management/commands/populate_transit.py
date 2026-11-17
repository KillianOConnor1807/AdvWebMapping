from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.core.management.base import BaseCommand
from django.db import transaction

from transit.models import TransitLine, TransitStop

# stop name -> (latitude, longitude)
# Entries marked APPROX were not in the supplied data (or looked misplaced) and are
# approximate positions; replace them with surveyed coordinates when available.
RED_LINE_STOPS = {
    "Tallaght": (53.287489316848806, -6.374559263148701),
    "Hospital": (53.290169910011784, -6.378720254134063),
    "Cookstown": (53.293522912550195, -6.384090071205439),
    "Belgard": (53.30011601433961, -6.374248439320472),
    "Kingswood": (53.303894160090856, -6.365401628877355),
    "Red Cow": (53.3168, -6.3698),  # APPROX - missing from supplied data
    "Kylemore": (53.3265, -6.3436),  # APPROX - missing from supplied data
    "Bluebell": (53.332991915483795, -6.330817357281772),
    "Blackhorse": (53.33306745106724, -6.329198814699201),
    "Drimnagh": (53.33623672457531, -6.314361434564502),
    "Goldenbridge": (53.336075834703536, -6.312998349616804),
    "Suir Road": (53.336699507422146, -6.306724166813667),
    "Rialto": (53.33802323469701, -6.296960914700724),
    "Fatima": (53.33862533270983, -6.2916064691956715),
    "James's": (53.34191149523813, -6.293419642480609),
    "Heuston": (53.34669657859404, -6.291831150149924),
    "Museum": (53.34863978608915, -6.284029833504144),
    "Smithfield": (53.34811854760356, -6.277622054134091),
    "Four Courts": (53.346767031436784, -6.273511662568616),
    "Jervis": (53.34769571713321, -6.265701069915186),
    "Abbey Street": (53.3486, -6.2583),  # APPROX - supplied point was near Connolly
    "Busáras": (53.350142226753256, -6.251614108168036),
    "George's Dock": (53.34947617523758, -6.247622981151133),
    "Mayor Square": (53.34920078550778, -6.243449463928353),
    "Spencer Dock": (53.34876528182899, -6.237194552508151),
    "The Point": (53.34824010971273, -6.2292552138215385),
    "Fettercairn": (53.29346237013435, -6.396003645359735),
    "Cheeverstown": (53.29093454874031, -6.4066322950411765),
    "Citywest Campus": (53.28818756285368, -6.419191707618616),
    "Fortunestown": (53.28413819570357, -6.424621946681261),
    "Saggart": (53.28467794863337, -6.4376963122717425),
}

GREEN_LINE_STOPS = {
    "Brides Glen": (53.242075487638786, -6.143055159839294),
    "Cherrywood": (53.245328093133004, -6.145964229655677),
    "Laughanstown": (53.25063070728073, -6.155008638506157),
    "Carrickmines": (53.253968577695275, -6.170136297407773),
    "Ballyogan Wood": (53.25500839952879, -6.184470022332739),
    "Leopardstown Valley": (53.25828174751856, -6.198460424474155),
    "The Gallops": (53.261118446547066, -6.205884779075949),
    "Glencairn": (53.26618809600607, -6.2099831944692285),
    "Central Park": (53.27014072214725, -6.2038677579589026),
    "Sandyford": (53.277762612160494, -6.204575861120312),
    "Stillorgan": (53.27934071249036, -6.210283601882188),
    "Kilmacud": (53.28296113220418, -6.22415215791486),
    "Balally": (53.286039923720125, -6.236769269076872),
    "Dundrum": (53.29247901206743, -6.24509484586692),
    "Windy Arbour": (53.30168701764439, -6.250609467619237),
    "Milltown": (53.309687906063466, -6.251746724249784),
    "Cowper": (53.31632853035641, -6.253549168696269),
    "Beechwood": (53.3210327490229, -6.25590951262485),
    "Ranelagh": (53.3261081091234, -6.256209920055514),
    "Charlemont": (53.33054214677247, -6.258741925349112),
    "Harcourt": (53.333553441318166, -6.262647221663272),
    "St. Stephen's Green": (53.33925509755208, -6.26135976133283),
    "Dawson": (53.34209923438092, -6.2580123645094075),
    "Westmoreland": (53.34626260605034, -6.258956502090066),
    "O'Connell - GPO": (53.348885091033665, -6.259900863280989),
    "O'Connell Upper": (53.35162152995879, -6.260870220344421),
    "Parnell": (53.353098169661855, -6.260627881093286),
    "Marlborough": (53.34918312877671, -6.257733414803557),
    "Trinity": (53.34526343391133, -6.258312771902198),
    "Dominick": (53.3513141414229, -6.26557564120973),
    "Broadstone - University": (53.35404384262424, -6.2741353468637175),
    "Grangegorman": (53.35712345819049, -6.277901703037795),
    "Phibsborough": (53.36034144881356, -6.279143691805727),
    "Cabra": (53.364324430258534, -6.282344589669755),
    "Broombridge": (53.37213264462782, -6.2976422561691034),
}

DART_STOPS = {
    "Bray": (53.20630298284682, -6.101152761697977),
    "Woodbrook": (53.22191178121155, -6.110894468233628),
    "Shankill": (53.23617545465145, -6.1170817682600225),
    "Killiney": (53.2557, -6.1128),  # APPROX - missing from supplied data
    "Dalkey": (53.2755267103896, -6.10344137406916),
    "Glenageary": (53.28094217307101, -6.123098467098592),
    "Sandycove & Glasthule": (53.2882018275142, -6.1268554541051765),
    "Dún Laoghaire": (53.29481867008718, -6.134436517413971),
    "Salthill & Monkstown": (53.29521965792343, -6.151678404426954),
    "Seapoint": (53.29914913943385, -6.165901284277454),
    "Blackrock": (53.3026, -6.1782),  # APPROX - missing from supplied data
    "Booterstown": (53.310013476439245, -6.195487557283744),
    "Sydney Parade": (53.32058028299198, -6.211925085328676),
    "Sandymount": (53.32799472346815, -6.221094906631861),
    "Lansdowne Road": (53.33361989680337, -6.2295161714900305),
    "Grand Canal Dock": (53.339616780821004, -6.238374094109433),
    "Pearse": (53.34319216147607, -6.248354852351815),
    "Tara Street": (53.3474375364633, -6.254592826027451),
    "Connolly": (53.351868661183474, -6.24879151050911),
    "Clontarf Road": (53.36294523869662, -6.226585058346439),
    "Killester": (53.37286953893368, -6.20362582594197),
    "Harmonstown": (53.37877431395983, -6.191584983307115),
    "Raheny": (53.3815, -6.1770),  # APPROX - missing from supplied data
    "Kilbarrack": (53.38717351056951, -6.161074714064185),
    "Howth Junction & Donaghmede": (53.39137248750445, -6.1562787855174745),  # supplied as "Howth"
    "Clongriffin": (53.40256773448898, -6.149748159411313),
    "Portmarnock": (53.41728743482986, -6.151074692715811),
    "Malahide": (53.4509, -6.1541),  # APPROX - missing from supplied data
    "Bayside": (53.3917984476719, -6.137707316754921),
    "Sutton": (53.39204185148738, -6.1141358381529995),  # supplied as "Burrow" (Burrow Road)
    "Howth": (53.38875578242482, -6.0741357524933175),
}

LINES = [
    {
        "name": "Luas Red Line",
        "mode": "luas",
        "colour": "#e4002b",
        "stops": RED_LINE_STOPS,
        # Each branch is an ordered list of stops; branches become parts of one MultiLineString
        "branches": [
            ["Tallaght", "Hospital", "Cookstown", "Belgard", "Kingswood", "Red Cow", "Kylemore",
             "Bluebell", "Blackhorse", "Drimnagh", "Goldenbridge", "Suir Road", "Rialto", "Fatima",
             "James's", "Heuston", "Museum", "Smithfield", "Four Courts", "Jervis", "Abbey Street",
             "Busáras", "George's Dock", "Mayor Square", "Spencer Dock", "The Point"],
            ["Saggart", "Fortunestown", "Citywest Campus", "Cheeverstown", "Fettercairn", "Belgard"],
        ],
    },
    {
        "name": "Luas Green Line",
        "mode": "luas",
        "colour": "#00a94f",
        "stops": GREEN_LINE_STOPS,
        "branches": [
            # Northbound through the city centre (Dawson -> Westmoreland -> O'Connell)
            ["Brides Glen", "Cherrywood", "Laughanstown", "Carrickmines", "Ballyogan Wood",
             "Leopardstown Valley", "The Gallops", "Glencairn", "Central Park", "Sandyford",
             "Stillorgan", "Kilmacud", "Balally", "Dundrum", "Windy Arbour", "Milltown", "Cowper",
             "Beechwood", "Ranelagh", "Charlemont", "Harcourt", "St. Stephen's Green", "Dawson",
             "Westmoreland", "O'Connell - GPO", "O'Connell Upper", "Dominick",
             "Broadstone - University", "Grangegorman", "Phibsborough", "Cabra", "Broombridge"],
            # Southbound half of the one-way city-centre loop
            ["Dominick", "Parnell", "Marlborough", "Trinity", "St. Stephen's Green"],
        ],
    },
    {
        "name": "DART",
        "mode": "dart",
        "colour": "#0072bc",
        "stops": DART_STOPS,
        "branches": [
            ["Bray", "Woodbrook", "Shankill", "Killiney", "Dalkey", "Glenageary",
             "Sandycove & Glasthule", "Dún Laoghaire", "Salthill & Monkstown", "Seapoint",
             "Blackrock", "Booterstown", "Sydney Parade", "Sandymount", "Lansdowne Road",
             "Grand Canal Dock", "Pearse", "Tara Street", "Connolly", "Clontarf Road", "Killester",
             "Harmonstown", "Raheny", "Kilbarrack", "Howth Junction & Donaghmede", "Clongriffin",
             "Portmarnock", "Malahide"],
            ["Howth Junction & Donaghmede", "Bayside", "Sutton", "Howth"],
        ],
    },
]


def to_point(lat, lng):
    return Point(lng, lat, srid=4326)  # x=lng, y=lat


class Command(BaseCommand):
    help = "Populate the database with Dublin transit lines and their stops"

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete existing lines first")

    @transaction.atomic
    def handle(self, *args, clear=False, **options):
        if clear:
            deleted, _ = TransitLine.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} existing lines/stops")

        for spec in LINES:
            stops = spec["stops"]
            route = MultiLineString(
                [LineString([to_point(*stops[name]) for name in branch]) for branch in spec["branches"]],
                srid=4326,
            )
            line, _ = TransitLine.objects.update_or_create(
                name=spec["name"],
                defaults={"mode": spec["mode"], "colour": spec["colour"], "route": route},
            )
            for name, (lat, lng) in stops.items():
                TransitStop.objects.update_or_create(
                    line=line, name=name, defaults={"location": to_point(lat, lng)},
                )
            self.stdout.write(f"{line.name}: {len(spec['branches'])} branches, {len(stops)} stops")

        self.stdout.write(self.style.SUCCESS(
            f"Done: {TransitLine.objects.count()} lines, {TransitStop.objects.count()} stops"))
