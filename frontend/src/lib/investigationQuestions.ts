import type { AnomalyType, IncidentUpdate } from "../types/events";

export interface InvestigationQuestion {
  id: string;
  label: string;
  prompt: string;
  scope: "general" | "category";
}

export interface InvestigationQuestionSet {
  profileId: string;
  profileLabel: string;
  questions: InvestigationQuestion[];
}

interface Profile {
  label: string;
  matches: string[];
  questions: Array<Pick<InvestigationQuestion, "id" | "label" | "prompt">>;
}

const GENERAL_QUESTIONS: InvestigationQuestion[] = [
  {
    id: "roles_and_actions",
    label: "Kim, kime/neye, ne yaptı?",
    scope: "general",
    prompt:
      "Seçili olayda kim, kime veya neye, ne yaptı? Kişileri geçici numaralarla ayır; "
      + "görülebilen asgari kişi sayısını, rollerini ve en ayırt edilebilir göründükleri "
      + "anları belirt. Kimlik veya suçluluk çıkarımı yapma; bulguları videoda göster.",
  },
  {
    id: "event_chain",
    label: "Olayın tam zinciri nedir?",
    scope: "general",
    prompt:
      "Seçili olayın öncesini, ilk kritik hareketini, dönüm noktasını, sonucunu ve "
      + "kişilerin son görüldüğü yönü zaman sırasıyla çıkar. Her önemli adımı kanıt "
      + "zamanıyla göster; kayıt dışında kalan kısmı açıkça belirt.",
  },
  {
    id: "proof_and_limits",
    label: "Görüntü neyi kanıtlıyor?",
    scope: "general",
    prompt:
      "Seçili olay için görüntünün desteklediği bulguları, iddiayı zayıflatan veya "
      + "alternatif açıklama oluşturan bulguları ve kamera kör noktalarını karşılaştır. "
      + "Görüntüden belirlenemeyen unsurları ayrıca yaz ve en güçlü kanıta götür.",
  },
];

const PROFILES: Record<string, Profile> = {
  abuse: {
    label: "Kötü muamele odağı",
    matches: ["abuse", "eziyet", "kotu muamele", "istismar"],
    questions: [
      {
        id: "abuse_pattern",
        label: "Davranış tek taraflı ve tekrarlı mı?",
        prompt:
          "Seçili olayda davranış tek bir temas mı, yoksa aynı kişiye yönelen tekrarlı "
          + "ve tek taraflı bir baskı örüntüsü mü? Geri çekilme, savunmasızlık ve eylemin "
          + "devam ettiği anları destekleyen ve zayıflatan kanıtlarla göster.",
      },
      {
        id: "abuse_safety",
        label: "Etkilenen kişi uzaklaşabildi mi?",
        prompt:
          "Seçili olayda etkilenen kişi olaydan uzaklaşabildi mi, takip edildi mi, yerde "
          + "kaldı mı veya yardım aldı mı? Tarafların son konumlarını ve devam eden saha "
          + "riskini görüntüyle belirle.",
      },
    ],
  },
  arrest: {
    label: "Kontrol altına alma odağı",
    matches: ["arrest", "gozalti", "kelepce", "kontrol altina"],
    questions: [
      {
        id: "arrest_sequence",
        label: "Kısıtlama öncesi ve sonrası ne oldu?",
        prompt:
          "Seçili olayda fiziksel kısıtlama başlamadan önceki davranışı ve kişi kontrol "
          + "altına alındıktan sonraki hareketleri sırala. Kaçma, teslim olma, direnme ve "
          + "devam eden teması yalnız görüntü bulgusu olarak göster; hukuki değerlendirme yapma.",
      },
      {
        id: "arrest_risk",
        label: "Kontrol sağlandı mı, risk sürüyor mu?",
        prompt:
          "Seçili olayda kontrolün sağlanıp sağlanmadığını; ellerin görünürlüğünü, silah "
          + "benzeri nesneyi, kaçma veya yeni çatışma riskini ve çevredeki kişileri kanıt "
          + "zamanlarıyla değerlendir.",
      },
    ],
  },
  arson: {
    label: "Kundaklama odağı",
    matches: ["arson", "kundak"],
    questions: [
      {
        id: "arson_cause",
        label: "İlk tutuşmaya ne yol açtı?",
        prompt:
          "Seçili olayda ilk alev veya dumandan hemen önce kaynak bölgesine kim ya da ne "
          + "temas etti? Yaklaşma, nesne bırakma, el hareketi, tutuşma ve uzaklaşma zincirinin "
          + "hangi kısmının görüntüyle kurulabildiğini göster.",
      },
      {
        id: "arson_spread",
        label: "Yangın nereden yayılıyor?",
        prompt:
          "Seçili olayda tek veya birden fazla olası başlangıç noktasını, yayılma yönünü "
          + "ve risk altındaki kişi, çıkış veya alanları belirle. Görüntüden çıkarılamayan "
          + "yangın nedenini kesinleştirme.",
      },
    ],
  },
  assault: {
    label: "Saldırı odağı",
    matches: ["assault", "saldiri"],
    questions: [
      {
        id: "assault_initiation",
        label: "İlk saldırı hareketi kimden geldi?",
        prompt:
          "Seçili olayda ilk saldırı görünümündeki hareketi ve öncesindeki taraf "
          + "davranışlarını çıkar. Yaklaşma, ilk temas, geri çekilme ve savunma hareketlerini "
          + "kanıtlarıyla ayır; meşru savunma veya suçluluk hükmü verme.",
      },
      {
        id: "assault_ongoing_risk",
        label: "Saldırı ve acil risk sürüyor mu?",
        prompt:
          "Seçili olayda saldırı görünümündeki temasın kişi düştükten veya uzaklaşmaya "
          + "çalıştıktan sonra sürüp sürmediğini; kullanılan nesneyi, etkilenen kişinin "
          + "hareketini ve tarafların son konumunu göster.",
      },
    ],
  },
  burglary: {
    label: "İzinsiz giriş odağı",
    matches: [
      "burglary", "konut hirsizligi", "kilit zorlama", "pencere zorlama",
      "zorla giris", "izinsiz giris",
    ],
    questions: [
      {
        id: "burglary_entry",
        label: "Giriş nasıl sağlandı?",
        prompt:
          "Seçili olayda girişin olağan yoldan mı yoksa kapı, pencere veya kilide müdahaleyle "
          + "mi gerçekleştiğini araştır. Müdahale, giriş ve varsa eşya ile çıkış zincirini "
          + "kanıt zamanlarıyla göster.",
      },
      {
        id: "burglary_exit",
        label: "Eşya, ortak ve çıkış bağlantısı nedir?",
        prompt:
          "Seçili olayda içeri giren kişinin çıkarken taşıdığı nesneyi, kullanılan rotayı, "
          + "birlikte hareket eden kişiyi veya aracı ve korunması gereken temas noktalarını "
          + "görüntüyle belirle.",
      },
    ],
  },
  explosion: {
    label: "Patlama odağı",
    matches: ["explosion", "patlama"],
    questions: [
      {
        id: "explosion_source",
        label: "Patlama kaynağıyla bağlantı kurulabiliyor mu?",
        prompt:
          "Seçili olayda patlama merkezinin patlama öncesindeki durumunu ve bu alanla son "
          + "temas eden kişi, araç veya bırakılan nesneyi çıkar. Görüntünün kurduğu ve "
          + "kuramadığı nedensellik bağlantısını ayrı yaz.",
      },
      {
        id: "explosion_secondary_risk",
        label: "İkincil tehlike var mı?",
        prompt:
          "Seçili olay sonrasında ikincil patlama görünümü, yangın, yoğun duman, düşen "
          + "parça, hareket etmeyen kişi veya erişimi kapanan alan var mı? Risk bölgelerini "
          + "kanıt zamanlarıyla göster.",
      },
    ],
  },
  fighting: {
    label: "Kavga odağı",
    matches: ["fighting", "kavga"],
    questions: [
      {
        id: "fighting_roles",
        label: "Karşılıklı mı, tek taraflı mı?",
        prompt:
          "Seçili olayda fiziksel temas karşılıklı mı, yoksa bir kişi geri çekilirken "
          + "tek taraflı mı sürüyor? Her kişinin ilk ve sonraki eylemini ayrı kanıtlarla "
          + "göster; toplu bir suçluluk değerlendirmesi yapma.",
      },
      {
        id: "fighting_participants",
        label: "Kim katıldı, kim müdahale etti?",
        prompt:
          "Seçili olayda aktif olarak fiziksel temasa katılanları, ayırmaya çalışanları, "
          + "sonradan katılanları ve yalnız izleyenleri ayır. Silah benzeri nesneyi ve "
          + "devam eden saha riskini ayrıca belirt.",
      },
    ],
  },
  road_accident: {
    label: "Trafik kazası odağı",
    matches: ["roadaccidents", "road accident", "trafik kazasi", "arac kazasi"],
    questions: [
      {
        id: "collision_chain",
        label: "Çarpışma nasıl oluştu?",
        prompt:
          "Seçili olayda araç ve yayaların çarpışma öncesi yönlerini, kaçınma hareketlerini, "
          + "görüş engelini ve ilk temas noktasını zaman sırasıyla çıkar. Kusur oranı veya "
          + "hukuki sorumluluk belirleme.",
      },
      {
        id: "collision_safety",
        label: "Çarpışma sonrası risk sürüyor mu?",
        prompt:
          "Seçili olay sonrasında hareket etmeyen kişi, araçtan çıkamama görünümü, açık "
          + "trafik şeridi, yaklaşan araç, duman veya ikincil çarpışma riski var mı? Acil "
          + "saha önceliklerini kanıtlarıyla göster.",
      },
    ],
  },
  robbery: {
    label: "Yağma odağı",
    matches: ["robbery", "yagma", "gasp"],
    questions: [
      {
        id: "robbery_link",
        label: "Tehdit ile eşyanın alınması bağlantılı mı?",
        prompt:
          "Seçili olayda cebir veya tehdit görünümündeki hareket ile eşyanın el değiştirmesi "
          + "arasında zaman ve davranış bağlantısı var mı? Destekleyen ve zayıflatan "
          + "kanıtları göster; nesnenin niteliğini görüntü izin vermiyorsa kesinleştirme.",
      },
      {
        id: "robbery_escape",
        label: "Ortak hareket ve kaçış durumu nedir?",
        prompt:
          "Seçili olayda tehdit eden, eşyayı alan, gözcülük veya kaçışa yardım eden kişiler "
          + "birlikte hareket ediyor mu? Silah benzeri nesneyi, son konumları ve kaçış "
          + "yönünü görüntüyle belirle.",
      },
    ],
  },
  shooting: {
    label: "Ateşli silah odağı",
    matches: ["shooting", "ates etme", "silahli olay", "silah"],
    questions: [
      {
        id: "shooting_evidence",
        label: "Gerçek ateşleme kanıtı var mı?",
        prompt:
          "Seçili olay yalnız silah benzeri bir nesne mi gösteriyor, yoksa ateşlemeyi "
          + "destekleyen yöneltme, parlama, geri tepme veya eşzamanlı çevre tepkisi var mı? "
          + "Her işareti ayrı değerlendir; görsel kanıt yoksa atış sayısı verme.",
      },
      {
        id: "shooting_danger_line",
        label: "Tehlike hattı ve son konum nedir?",
        prompt:
          "Seçili olayda olası ateşleme yönünü, bu hattaki kişi ve alanları, hareket "
          + "etmeyen kişileri ve silah benzeri nesneyi taşıyan kişinin son görüldüğü konumu "
          + "kanıt zamanlarıyla göster.",
      },
    ],
  },
  shoplifting: {
    label: "Mağaza hırsızlığı odağı",
    matches: ["shoplifting", "magaza", "raf", "urun", "kasa"],
    questions: [
      {
        id: "shoplifting_continuity",
        label: "Ürün takibi kesintisiz mi?",
        prompt:
          "Seçili olayda ürünün raftan alınmasından gizlenmesine, geri bırakılmasına veya "
          + "çıkışa kadar kişi-ürün takibi kesintisiz mi? Kör noktaları ve aynı ürün "
          + "olduğunu zayıflatan anları açıkça belirt.",
      },
      {
        id: "shoplifting_checkout",
        label: "Ödeme veya geri bırakma ihtimali var mı?",
        prompt:
          "Seçili olayda ürün için ödeme, ürünü geri bırakma, başka kişiye aktarma veya "
          + "kasa alanının görüntü dışında kalması ihtimali var mı? Kişinin son konumunu "
          + "ve birlikte hareket edenleri de göster.",
      },
    ],
  },
  stealing: {
    label: "Eşya alma odağı",
    matches: ["stealing", "calma"],
    questions: [
      {
        id: "stealing_object_chain",
        label: "Hangi nesne nereden alındı?",
        prompt:
          "Seçili olayda hangi nesnenin hangi konumdan, hangi kişi tarafından alındığını "
          + "ve görüntü boyunca aynı nesne olduğunun ne ölçüde izlenebildiğini kanıt "
          + "zamanlarıyla çıkar. Mülkiyet veya izin hakkında görüntü dışı varsayım yapma.",
      },
      {
        id: "stealing_transfer",
        label: "Nesne nereye aktarıldı?",
        prompt:
          "Seçili olayda alınan nesne başka kişiye, çantaya veya araca aktarıldı mı? "
          + "Birlikte hareket eden kişileri, görsel takip kopukluklarını ve son görülen "
          + "çıkış yönünü göster.",
      },
    ],
  },
  vandalism: {
    label: "Mala zarar odağı",
    matches: ["vandalism", "vandalizm", "mala zarar"],
    questions: [
      {
        id: "vandalism_cause",
        label: "Hasarı hangi hareket oluşturdu?",
        prompt:
          "Seçili olayda fiziksel hasardan önceki durumu, kişinin temasını, kullanılan "
          + "nesneyi ve hasarın ilk görünür olduğu anı karşılaştır. Nedensellik bağını "
          + "destekleyen ve zayıflatan kareleri göster.",
      },
      {
        id: "vandalism_intent_risk",
        label: "Kasıtlı tekrar mı, kazara temas mı?",
        prompt:
          "Seçili olayda hedefe tekrar yönelme ve hazırlık hareketi var mı, yoksa tek bir "
          + "kazara temas açıklaması mümkün mü? Zarar verme sürüyorsa son konumu ve "
          + "korunması gereken temas alanlarını belirt.",
      },
    ],
  },
  theft_generic: {
    label: "Olası hırsızlık odağı",
    matches: [],
    questions: [
      {
        id: "theft_legal_fork",
        label: "Eşya hangi koşulda alındı?",
        prompt:
          "Seçili olayda eşyanın alınma zincirini çıkar. Cebir veya tehdit, izinsiz giriş, "
          + "ürün gizleme, ödeme alanını geçme ya da yalnız eşyanın taşınması işaretlerinden "
          + "hangilerinin görüntüyle desteklendiğini ve hangilerinin belirlenemediğini göster.",
      },
      {
        id: "theft_route",
        label: "Kişi, eşya ve çıkış rotası nedir?",
        prompt:
          "Seçili olayda eşyayı alan kişiyi görüntü boyunca takip et; nesnenin başka kişi, "
          + "çanta veya araca aktarılmasını, birlikte hareket edenleri, takip kopukluklarını "
          + "ve son görülen çıkış yönünü belirt.",
      },
    ],
  },
  unknown: {
    label: "Belirsiz olay odağı",
    matches: [],
    questions: [
      {
        id: "unknown_observable_change",
        label: "Somut olarak ne değişti?",
        prompt:
          "Seçili olayda olağandışı kabul edilen somut hareketi, bu hareketten önceki "
          + "durumu ve görünür sonucunu çıkar. En yakın olağan alternatif açıklamayı da "
          + "kanıtlarıyla karşılaştır.",
      },
      {
        id: "unknown_priority",
        label: "Hangi bilgi eksik, risk sürüyor mu?",
        prompt:
          "Seçili olayı sınıflandırmayı engelleyen kamera kör noktalarını ve eksik görsel "
          + "bilgiyi belirt. Hâlen süren kişi, araç, ateş, duman veya fiziksel temas riski "
          + "varsa son konumuyla göster.",
      },
    ],
  },
};

const PROFILE_CANDIDATES: Record<AnomalyType, string[]> = {
  kavga: ["abuse", "fighting"],
  saldiri: ["abuse", "assault"],
  hirsizlik: ["robbery", "burglary", "shoplifting", "stealing"],
  silahli_olay: ["shooting"],
  yangin: ["arson"],
  patlama: ["explosion"],
  arac_kazasi: ["road_accident"],
  vandalizm: ["vandalism"],
  normal: ["unknown"],
  bilinmeyen: [
    "arrest", "abuse", "robbery", "burglary", "shoplifting", "shooting",
    "arson", "explosion", "road_accident", "vandalism", "fighting", "assault",
    "stealing",
  ],
};

const DEFAULT_PROFILE: Record<AnomalyType, string> = {
  kavga: "fighting",
  saldiri: "assault",
  hirsizlik: "theft_generic",
  silahli_olay: "shooting",
  yangin: "arson",
  patlama: "explosion",
  arac_kazasi: "road_accident",
  vandalizm: "vandalism",
  normal: "unknown",
  bilinmeyen: "unknown",
};

function normalize(text: string): string {
  return text
    .toLocaleLowerCase("tr-TR")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replaceAll("ı", "i")
    .replaceAll("ş", "s")
    .replaceAll("ğ", "g")
    .replaceAll("ü", "u")
    .replaceAll("ö", "o")
    .replaceAll("ç", "c");
}

function resolveProfile(incident: IncidentUpdate): string {
  const text = normalize(`${incident.title} ${incident.detail}`);
  const candidates = PROFILE_CANDIDATES[incident.anomaly_type];
  return candidates.find((id) => PROFILES[id].matches.some((term) => text.includes(term)))
    ?? DEFAULT_PROFILE[incident.anomaly_type];
}

export function investigationQuestionsFor(
  incident: IncidentUpdate,
): InvestigationQuestionSet {
  const profileId = resolveProfile(incident);
  const profile = PROFILES[profileId];
  return {
    profileId,
    profileLabel: profile.label,
    questions: [
      ...GENERAL_QUESTIONS,
      ...profile.questions.map((question) => ({ ...question, scope: "category" as const })),
    ],
  };
}
