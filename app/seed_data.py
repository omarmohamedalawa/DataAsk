"""Generate SYNTHETIC Egyptian retail data (no real company, patient or customer data)."""
import random, sqlite3, os, datetime as dt
random.seed(42)
DB = os.path.join(os.path.dirname(__file__), "..", "data", "retail.db")

CITIES = [("Cairo","القاهرة",0.34),("Giza","الجيزة",0.20),("Alexandria","الإسكندرية",0.18),
          ("Mansoura","المنصورة",0.09),("Tanta","طنطا",0.07),("Assiut","أسيوط",0.06),("Aswan","أسوان",0.06)]
CATS = {  # en: (ar, [(name_en, name_ar, cost, price)])
 "Groceries":("بقالة",[("Rice 1kg","أرز 1 كجم",32,42),("Sugar 1kg","سكر 1 كجم",30,38),("Cooking oil 1L","زيت طعام 1 لتر",60,78),
    ("Pasta 400g","مكرونة 400 جم",9,13),("Tea 100 bags","شاي 100 كيس",55,72),("Lentils 500g","عدس 500 جم",22,30)]),
 "Dairy":("ألبان",[("Full-cream milk 1L","لبن كامل الدسم 1 لتر",34,44),("White cheese 500g","جبنة بيضاء 500 جم",60,80),
    ("Yogurt 170g","زبادي 170 جم",6,9),("Butter 200g","زبدة 200 جم",48,65)]),
 "Beverages":("مشروبات",[("Mineral water 1.5L","مياه معدنية 1.5 لتر",5,8),("Orange juice 1L","عصير برتقال 1 لتر",22,32),
    ("Soft drink 1L","مشروب غازي 1 لتر",12,18),("Hibiscus drink 250ml","كركديه 250 مل",7,12)]),
 "Personal Care":("عناية شخصية",[("Shampoo 400ml","شامبو 400 مل",55,85),("Toothpaste 100ml","معجون أسنان 100 مل",20,32),
    ("Soap bar x4","صابون 4 قطع",25,40),("Deodorant 150ml","مزيل عرق 150 مل",45,70)]),
 "OTC Pharmacy":("صيدلية (بدون روشتة)",[("Vitamin C 1000mg x20","فيتامين سي 1000 مجم x20",40,65),("Pain relief tablets x24","مسكن ألم أقراص x24",18,30),
    ("Oral rehydration x10","أملاح معالجة الجفاف x10",15,25),("Sunscreen SPF50","واقي شمس SPF50",110,170),("Bandages x50","شاش لاصق x50",12,22)]),
 "Cleaning":("منظفات",[("Dish soap 750ml","سائل غسيل أطباق 750 مل",22,35),("Laundry powder 3kg","مسحوق غسيل 3 كجم",120,165),
    ("Floor cleaner 1L","منظف أرضيات 1 لتر",25,40)]),
 "Baby":("مستلزمات أطفال",[("Diapers size 4 x44","حفاضات مقاس 4 x44",180,250),("Baby wipes x80","مناديل مبللة x80",20,35),
    ("Infant formula 400g","لبن أطفال 400 جم",170,230)]),
}
PAY = [("cash",0.30),("card",0.30),("mobile_wallet",0.20),("cash_on_delivery",0.20)]

def wchoice(items):
    r=random.random(); a=0
    for v,w in items:
        a+=w
        if r<=a: return v
    return items[-1][0]

def build():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if os.path.exists(DB): os.remove(DB)
    c = sqlite3.connect(DB); x = c.cursor()
    x.executescript("""
    CREATE TABLE stores(store_id INTEGER PRIMARY KEY, city TEXT, city_ar TEXT, name TEXT);
    CREATE TABLE products(product_id INTEGER PRIMARY KEY, name_en TEXT, name_ar TEXT, category TEXT, category_ar TEXT, unit_cost REAL, unit_price REAL);
    CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, city TEXT, joined TEXT);
    CREATE TABLE orders(order_id INTEGER PRIMARY KEY, order_date TEXT, customer_id INT, store_id INT, channel TEXT, payment TEXT);
    CREATE TABLE order_items(order_id INT, product_id INT, qty INT, unit_price REAL, discount REAL);
    CREATE TABLE returns(order_id INT, product_id INT, qty INT, reason TEXT);
    """)
    sid=1; stores=[]
    for en,ar,w in CITIES:
        for k in range(2 if w>=0.18 else 1):
            x.execute("INSERT INTO stores VALUES(?,?,?,?)",(sid,en,ar,f"{en} Store {k+1}")); stores.append((sid,en,w)); sid+=1
    pid=1; prods=[]
    for cat,(car,items) in CATS.items():
        for ne,na,cost,price in items:
            x.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?)",(pid,ne,na,cat,car,cost,price)); prods.append((pid,cat,price)); pid+=1
    cust=[]
    for i in range(1,2501):
        city=wchoice([(e,w) for e,_,w in CITIES])
        x.execute("INSERT INTO customers VALUES(?,?,?)",(i,city,"2025-01-01")); cust.append((i,city))
    bycity={}
    for s,e,w in stores: bycity.setdefault(e,[]).append(s)
    pop = {p[0]:random.random()**2+0.1 for p in prods}  # skewed popularity
    start=dt.date(2025,1,1); end=dt.date(2026,10,31)
    oid=1; d=start
    # Ramadan 2025: ~Mar 1-30, Ramadan 2026: ~Feb 18-Mar 19 -> grocery/dairy bump
    ram=[(dt.date(2025,3,1),dt.date(2025,3,30)),(dt.date(2026,2,18),dt.date(2026,3,19))]
    while d<=end:
        growth=1+0.0009*(d-start).days
        base=int(26*growth*(1.25 if d.weekday() in (3,4) else 1))  # Thu/Fri busier
        inram=any(a<=d<=b for a,b in ram)
        if inram: base=int(base*1.35)
        if d.month in (11,) and d.day>=20: base=int(base*1.4)  # Black Friday
        for _ in range(base):
            ci,city=random.choice(cust)
            channel="online" if random.random()<0.28+0.0002*(d-start).days else "store"
            store=random.choice(bycity[city]); pay=wchoice(PAY)
            if channel=="store" and pay=="cash_on_delivery": pay="cash"
            x.execute("INSERT INTO orders VALUES(?,?,?,?,?,?)",(oid,d.isoformat(),ci,store,channel,pay))
            for _ in range(random.choice([1,1,2,2,3,4,5])):
                p=random.choices(prods,weights=[pop[q[0]]*(1.8 if inram and q[1] in("Groceries","Dairy") else 1) for q in prods])[0]
                qty=random.choice([1,1,1,2,2,3,6]); disc=random.choice([0,0,0,0,0.05,0.10,0.15]) if d.month==11 or inram else random.choice([0,0,0,0,0,0.05])
                x.execute("INSERT INTO order_items VALUES(?,?,?,?,?)",(oid,p[0],qty,p[2],disc))
                if random.random()<0.025:
                    x.execute("INSERT INTO returns VALUES(?,?,?,?)",(oid,p[0],1,random.choice(["damaged","wrong item","expired","changed mind"])))
            oid+=1
        d+=dt.timedelta(days=1)
    x.executescript("""
    CREATE VIEW sales_fact AS
    SELECT o.order_id, o.order_date, substr(o.order_date,1,7) AS month, substr(o.order_date,1,4) AS year,
           o.channel, o.payment, o.customer_id, s.city, p.name_en AS product, p.name_ar AS product_ar,
           p.category, p.category_ar, i.qty,
           i.qty*i.unit_price*(1-i.discount) AS revenue,
           i.qty*(i.unit_price*(1-i.discount)-p.unit_cost) AS profit
    FROM order_items i JOIN orders o USING(order_id) JOIN products p USING(product_id) JOIN stores s ON s.store_id=o.store_id;
    CREATE VIEW returns_fact AS
    SELECT r.order_id, substr(o.order_date,1,7) AS month, substr(o.order_date,1,4) AS year, s.city, p.category, p.category_ar,
           p.name_en AS product, p.name_ar AS product_ar, r.qty, r.qty*i.unit_price AS revenue, r.reason
    FROM returns r JOIN orders o USING(order_id) JOIN products p USING(product_id) JOIN stores s ON s.store_id=o.store_id
    JOIN order_items i ON i.order_id=r.order_id AND i.product_id=r.product_id;
    """)
    c.commit()
    n=x.execute("SELECT count(*), round(sum(revenue)) FROM sales_fact").fetchone()
    print("orders",oid-1,"line items",n[0],"revenue EGP",n[1]); c.close()

if __name__=="__main__": build()
