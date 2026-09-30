"""Generate the SAMPLE citizen-request layer used for the demo.
These requests are SYNTHETIC (clearly flagged `synthetic: true` in the data and in the UI).
District populations are Census of India 2011 figures (data.gov.in / censusindia.gov.in)."""
import json, random
random.seed(26124)
D = [  # name, state, lat, lon, census2011_pop, languages
 ("Varanasi","Uttar Pradesh",25.3176,82.9739,3676841,["hi","en"]),
 ("Khordha (Bhubaneswar)","Odisha",20.2961,85.8245,2251673,["or","en"]),
 ("Patna","Bihar",25.5941,85.1376,5838465,["hi"]),
 ("Lucknow","Uttar Pradesh",26.8467,80.9462,4589838,["hi","ur"]),
 ("Kolkata","West Bengal",22.5726,88.3639,4496694,["bn"]),
 ("Chennai","Tamil Nadu",13.0827,80.2707,4646732,["ta","en"]),
 ("Bengaluru Urban","Karnataka",12.9716,77.5946,9621551,["kn","en"]),
 ("Hyderabad","Telangana",17.3850,78.4867,3943323,["te","ur"]),
 ("Pune","Maharashtra",18.5204,73.8567,9429408,["mr"]),
 ("Ahmedabad","Gujarat",23.0225,72.5714,7214225,["gu"]),
 ("Kamrup Metropolitan (Guwahati)","Assam",26.1445,91.7362,1253938,["as","bn"]),
 ("Jaipur","Rajasthan",26.9124,75.7873,6626178,["hi"]),
 ("Thiruvananthapuram","Kerala",8.5241,76.9366,3301427,["ml"]),
 ("Ludhiana","Punjab",30.9010,75.8573,3498739,["pa"]),
]
T = {
 "hi":[("pothole","मुख्य सड़क पर बड़ा गड्ढा है, रोज़ बाइक वाले गिरते हैं। कृपया जल्दी भरवाइए।",4),
       ("waterlogging","हल्की बारिश में भी गली में घुटनों तक पानी भर जाता है, स्कूल के बच्चे निकल नहीं पाते।",4),
       ("streetlight","चौराहे की स्ट्रीट लाइट दो महीने से खराब है, रात में महिलाओं को डर लगता है।",3),
       ("new_road","हमारे मोहल्ले तक पक्की सड़क नहीं है, एम्बुलेंस अंदर नहीं आ पाती।",5)],
 "en":[("pothole","Deep pothole near the bus stop, two-wheelers swerve into traffic to avoid it.",4),
       ("footpath","Footpath is broken and encroached; elderly people walk on the carriageway.",3),
       ("drainage","Open drain beside the school road overflows every evening.",3)],
 "or":[("pothole","ରାସ୍ତାରେ ବହୁତ ବଡ଼ ଗାତ ଅଛି, ରାତିରେ ଦୁର୍ଘଟଣା ହେଉଛି।",4),
       ("waterlogging","ବର୍ଷା ହେଲେ ଛକରେ ପାଣି ଜମି ରହୁଛି, ବସ୍ ଚଳାଚଳ ବନ୍ଦ ହେଉଛି।",4)],
 "bn":[("waterlogging","বৃষ্টি হলেই রাস্তায় হাঁটু জল জমে যায়, দোকান বন্ধ রাখতে হয়।",4),
       ("pothole","বাস স্ট্যান্ডের সামনে বড় গর্ত, প্রতিদিন দুর্ঘটনা ঘটছে।",4)],
 "ta":[("pothole","பிரதான சாலையில் பெரிய பள்ளம் உள்ளது, இருசக்கர வாகனங்கள் விழுகின்றன.",4),
       ("drainage","மழைநீர் வடிகால் அடைப்பால் தெருவில் தண்ணீர் தேங்குகிறது.",3)],
 "kn":[("pothole","ಮುಖ್ಯ ರಸ್ತೆಯಲ್ಲಿ ದೊಡ್ಡ ಗುಂಡಿ ಇದೆ, ರಾತ್ರಿ ಅಪಘಾತಗಳು ಆಗುತ್ತಿವೆ.",4),
       ("streetlight","ಬೀದಿ ದೀಪಗಳು ಕೆಲಸ ಮಾಡುತ್ತಿಲ್ಲ, ರಸ್ತೆ ಕತ್ತಲಾಗಿದೆ.",3)],
 "te":[("pothole","ప్రధాన రహదారిపై పెద్ద గుంత ఉంది, ద్విచక్ర వాహనదారులు పడిపోతున్నారు.",4),
       ("waterlogging","వర్షం పడితే కాలనీలో మోకాళ్ల లోతు నీరు నిలుస్తోంది.",4)],
 "ur":[("pothole","سڑک پر بڑا گڑھا ہے، روز حادثے ہو رہے ہیں۔",4)],
 "mr":[("pothole","रस्त्यावर मोठा खड्डा आहे, दुचाकीस्वार रोज पडतात.",4),
       ("footpath","पदपथ तुटलेला आहे, ज्येष्ठ नागरिकांना रस्त्यावरून चालावे लागते.",3)],
 "gu":[("pothole","મુખ્ય રસ્તા પર મોટો ખાડો છે, રોજ અકસ્માત થાય છે.",4),
       ("drainage","ગટર ઉભરાય છે અને રસ્તા પર ગંદુ પાણી ભરાય છે.",3)],
 "as":[("waterlogging","বৰষুণ হ'লেই পথত আঁঠুলৈকে পানী জমা হয়, বাট বন্ধ হৈ যায়।",5)],
 "ml":[("pothole","പ്രധാന റോഡിൽ വലിയ കുഴിയുണ്ട്, ഇരുചക്രവാഹനങ്ങൾ അപകടത്തിൽപ്പെടുന്നു.",4),
       ("drainage","ഓട അടഞ്ഞതിനാൽ മഴവെള്ളം റോഡിൽ കെട്ടിക്കിടക്കുന്നു.",3)],
 "pa":[("pothole","ਮੁੱਖ ਸੜਕ 'ਤੇ ਵੱਡਾ ਟੋਆ ਹੈ, ਰੋਜ਼ ਹਾਦਸੇ ਹੁੰਦੇ ਹਨ।",4),
       ("streetlight","ਗਲੀ ਦੀਆਂ ਬੱਤੀਆਂ ਬੰਦ ਹਨ, ਰਾਤ ਨੂੰ ਹਨੇਰਾ ਰਹਿੰਦਾ ਹੈ।",3)],
}
EN = {"pothole":"Pothole on a main road causing falls/accidents","waterlogging":"Recurring waterlogging blocks movement",
      "streetlight":"Streetlights not working, safety concern at night","new_road":"No paved road access; ambulances cannot enter",
      "footpath":"Broken/encroached footpath forces pedestrians onto road","drainage":"Blocked/overflowing drain floods the street"}
CH = ["whatsapp","voice","web","ivr","sms"]
EVB=[(e["lat"],e["lon"]) for e in json.load(open("web/data/evidence.json"))["events"] if e["city"]=="Bhubaneswar"]
out=[]; n=0
for name,state,lat,lon,pop,langs in D:
    hot = [(lat+random.uniform(-.03,.03), lon+random.uniform(-.03,.03)) for _ in range(3)]
    if name=="Varanasi": hot[0]=(25.2821,82.9965); hot[1]=(25.2870,82.9990)
    if name.startswith("Khordha"): hot[0]=EVB[len(EVB)//3]; hot[1]=EVB[2*len(EVB)//3]
    k = random.randint(6,12) + (6 if name.startswith(("Varanasi","Khordha")) else 0)
    for _ in range(k):
        lg = random.choice(langs); cat,txt,sev = random.choice(T[lg])
        h = random.choice(hot); n+=1
        out.append(dict(id=f"CR-{n:05d}", synthetic=True, district=name, state=state,
            lat=round(h[0]+random.gauss(0,.002),5), lon=round(h[1]+random.gauss(0,.002),5),
            lang=lg, channel=random.choice(CH), text=txt, category=cat, severity=max(1,min(5,sev+random.choice([-1,0,0,1]))),
            summary_en=EN[cat], vulnerable=random.random()<.35, days_open=random.randint(1,120)))
json.dump(dict(requests=out, districts=[dict(name=a,state=b,lat=c,lon=d,pop2011=e,langs=f) for a,b,c,d,e,f in D]),
          open("web/data/citizen_seed.json","w"), ensure_ascii=False, separators=(",",":"))
print(n,"synthetic requests")

# ---- SAMPLE public-investment layer (synthetic, flagged): sanctioned works per district,
# shaped like State PWD / Smart City / AMRUT work lists (scheme, cost in INR lakh, status).
SCHEMES = ["PWD maintenance","Smart Cities Mission","AMRUT 2.0 drainage","PMGSY road","State road fund","Municipal own funds"]
KIND = {"PWD maintenance":"pothole","Smart Cities Mission":"footpath","AMRUT 2.0 drainage":"drainage","PMGSY road":"new_road","State road fund":"pothole","Municipal own funds":"streetlight"}
seed = json.load(open("web/data/citizen_seed.json"))
works=[]; w=0
for d in seed["districts"]:
    rq=[r for r in seed["requests"] if r["district"]==d["name"]]
    for j in range(random.randint(3,6)):
        sch=random.choice(SCHEMES); w+=1
        if j==0 and rq:  # one work aligned with a real hotspot
            r=random.choice(rq); lat,lon=r["lat"]+random.gauss(0,.003),r["lon"]+random.gauss(0,.003)
        else:            # the rest placed where no citizen demand exists (misalignment is the point)
            lat,lon=d["lat"]+random.uniform(-.08,.08),d["lon"]+random.uniform(-.08,.08)
        works.append(dict(id=f"WK-{w:04d}",synthetic=True,district=d["name"],state=d["state"],lat=round(lat,5),lon=round(lon,5),
            scheme=sch,category=KIND[sch],cost_lakh=random.choice([35,60,85,120,180,240,400,650]),
            status=random.choice(["sanctioned","tendered","in progress"])))
seed["works"]=works
json.dump(seed,open("web/data/citizen_seed.json","w"),ensure_ascii=False,separators=(",",":"))
print(len(works),"sample sanctioned works")
