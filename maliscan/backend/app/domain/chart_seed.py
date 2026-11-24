"""Tek Düzen Hesap Planı (TDHP) — main accounts and the default sub-accounts
used by MaliScan's accounting engine. Offices adjust per taxpayer; imported
Luca charts replace this seed."""

# (code, name, class, normal_balance)
MAIN_ACCOUNTS: list[tuple[str, str, str, str]] = [
    ("100", "Kasa", "asset", "debit"),
    ("101", "Alınan Çekler", "asset", "debit"),
    ("102", "Bankalar", "asset", "debit"),
    ("103", "Verilen Çekler ve Ödeme Emirleri (-)", "asset", "credit"),
    ("108", "Diğer Hazır Değerler", "asset", "debit"),
    ("120", "Alıcılar", "asset", "debit"),
    ("121", "Alacak Senetleri", "asset", "debit"),
    ("126", "Verilen Depozito ve Teminatlar", "asset", "debit"),
    ("131", "Ortaklardan Alacaklar", "asset", "debit"),
    ("136", "Diğer Çeşitli Alacaklar", "asset", "debit"),
    ("150", "İlk Madde ve Malzeme", "asset", "debit"),
    ("153", "Ticari Mallar", "asset", "debit"),
    ("157", "Diğer Stoklar", "asset", "debit"),
    ("159", "Verilen Sipariş Avansları", "asset", "debit"),
    ("180", "Gelecek Aylara Ait Giderler", "asset", "debit"),
    ("190", "Devreden KDV", "asset", "debit"),
    ("191", "İndirilecek KDV", "asset", "debit"),
    ("193", "Peşin Ödenen Vergiler ve Fonlar", "asset", "debit"),
    ("195", "İş Avansları", "asset", "debit"),
    ("196", "Personel Avansları", "asset", "debit"),
    ("252", "Binalar", "asset", "debit"),
    ("253", "Tesis, Makine ve Cihazlar", "asset", "debit"),
    ("254", "Taşıtlar", "asset", "debit"),
    ("255", "Demirbaşlar", "asset", "debit"),
    ("257", "Birikmiş Amortismanlar (-)", "asset", "credit"),
    ("260", "Haklar", "asset", "debit"),
    ("300", "Banka Kredileri", "liability", "credit"),
    ("309", "Diğer Mali Borçlar", "liability", "credit"),
    ("320", "Satıcılar", "liability", "credit"),
    ("321", "Borç Senetleri", "liability", "credit"),
    ("331", "Ortaklara Borçlar", "liability", "credit"),
    ("335", "Personele Borçlar", "liability", "credit"),
    ("336", "Diğer Çeşitli Borçlar", "liability", "credit"),
    ("340", "Alınan Sipariş Avansları", "liability", "credit"),
    ("360", "Ödenecek Vergi ve Fonlar", "liability", "credit"),
    ("361", "Ödenecek Sosyal Güvenlik Kesintileri", "liability", "credit"),
    ("368", "Vadesi Geçmiş Ertelenmiş veya Taksitlendirilmiş Vergi ve Diğer Yükümlülükler", "liability", "credit"),
    ("391", "Hesaplanan KDV", "liability", "credit"),
    ("500", "Sermaye", "equity", "credit"),
    ("570", "Geçmiş Yıllar Karları", "equity", "credit"),
    ("580", "Geçmiş Yıllar Zararları (-)", "equity", "debit"),
    ("590", "Dönem Net Karı", "equity", "credit"),
    ("591", "Dönem Net Zararı (-)", "equity", "debit"),
    ("600", "Yurt İçi Satışlar", "income", "credit"),
    ("601", "Yurt Dışı Satışlar", "income", "credit"),
    ("602", "Diğer Gelirler", "income", "credit"),
    ("610", "Satıştan İadeler (-)", "income", "debit"),
    ("611", "Satış İskontoları (-)", "income", "debit"),
    ("621", "Satılan Ticari Mallar Maliyeti (-)", "expense", "debit"),
    ("642", "Faiz Gelirleri", "income", "credit"),
    ("649", "Diğer Olağan Gelir ve Karlar", "income", "credit"),
    ("653", "Komisyon Giderleri (-)", "expense", "debit"),
    ("656", "Kambiyo Zararları (-)", "expense", "debit"),
    ("659", "Diğer Olağan Gider ve Zararlar (-)", "expense", "debit"),
    ("660", "Kısa Vadeli Borçlanma Giderleri (-)", "expense", "debit"),
    ("679", "Diğer Olağandışı Gelir ve Karlar", "income", "credit"),
    ("689", "Diğer Olağandışı Gider ve Zararlar (-)", "expense", "debit"),
    ("740", "Hizmet Üretim Maliyeti", "cost", "debit"),
    ("760", "Pazarlama Satış ve Dağıtım Giderleri", "cost", "debit"),
    ("770", "Genel Yönetim Giderleri", "cost", "debit"),
    ("780", "Finansman Giderleri", "cost", "debit"),
]

# Default postable sub-accounts referenced by taxpayer defaults and the engine.
SUB_ACCOUNTS: list[tuple[str, str]] = [
    ("100.01", "Merkez Kasa TL"),
    ("102.01", "Banka Vadesiz TL"),
    ("120.01", "Yurt İçi Alıcılar"),
    ("153.01", "Ticari Mallar"),
    ("191.01", "İndirilecek KDV %1"),
    ("191.10", "İndirilecek KDV %10"),
    ("191.20", "İndirilecek KDV %20"),
    ("300.01", "Kredi Kartı Borçları"),
    ("320.01", "Yurt İçi Satıcılar"),
    ("360.01", "Ödenecek Gelir Vergisi Stopajı"),
    ("360.02", "Ödenecek KDV Tevkifatı"),
    ("391.01", "Hesaplanan KDV %1"),
    ("391.10", "Hesaplanan KDV %10"),
    ("391.20", "Hesaplanan KDV %20"),
    ("600.01", "Yurt İçi Satışlar"),
    ("689.01", "Kanunen Kabul Edilmeyen Giderler (KKEG)"),
    ("760.01", "Pazarlama Satış Dağıtım Giderleri"),
    ("770.01", "Genel Yönetim Giderleri"),
    ("770.02", "Akaryakıt ve Taşıt Giderleri"),
    ("770.03", "Kırtasiye ve Büro Giderleri"),
    ("770.04", "Yemek ve İkram Giderleri"),
    ("770.05", "Haberleşme Giderleri"),
    ("770.06", "Kira Giderleri"),
    ("770.07", "Elektrik Su Doğalgaz Giderleri"),
    ("770.08", "Bakım Onarım Giderleri"),
    ("770.09", "Temizlik Giderleri"),
    ("770.10", "Danışmanlık ve Hizmet Giderleri"),
    ("770.11", "Yazılım ve Abonelik Giderleri"),
    ("770.99", "Diğer Genel Yönetim Giderleri"),
    ("780.01", "Banka Masrafları"),
]

_MAIN_BY_CODE = {c: (n, k, b) for c, n, k, b in MAIN_ACCOUNTS}


def seed_rows() -> list[dict]:
    rows = []
    for code, name, klass, balance in MAIN_ACCOUNTS:
        rows.append({"code": code, "name": name, "parent_code": None, "account_class": klass,
                     "normal_balance": balance, "is_postable": False})
    for code, name in SUB_ACCOUNTS:
        parent = code.split(".")[0]
        _, klass, balance = _MAIN_BY_CODE[parent]
        rows.append({"code": code, "name": name, "parent_code": parent, "account_class": klass,
                     "normal_balance": balance, "is_postable": True})
    return rows


def classify(code: str) -> tuple[str, str]:
    """Account class + normal balance from the TDHP main-account digit."""
    main = code.split(".")[0]
    if main in _MAIN_BY_CODE:
        _, k, b = _MAIN_BY_CODE[main]
        return k, b
    first = main[0]
    return {
        "1": ("asset", "debit"), "2": ("asset", "debit"), "3": ("liability", "credit"),
        "4": ("liability", "credit"), "5": ("equity", "credit"), "6": ("income", "credit"),
        "7": ("cost", "debit"), "8": ("memo", "debit"), "9": ("memo", "debit"),
    }.get(first, ("memo", "debit"))
