import type {
  DevelopmentUse,
  LearningCandidateSummary,
  LearningRoute,
  LearningRouteSummary,
} from "../types/domain";

export interface OperatorDevelopmentPresentation {
  category: string;
  title: string;
  description: string;
  action: string;
  technicalComponent: string;
  technicalType: string;
}

export const DEVELOPMENT_PRESENTATION: Record<
  DevelopmentUse,
  OperatorDevelopmentPresentation
> = {
  camera_rule: {
    category: "Kamera önerisi",
    title: "Bu kameraya özel tekrar eden bir durum tespit edildi",
    description: "Aynı kamera kaydında benzer yanlış uyarılar tekrar ediyor olabilir.",
    action: "Bu kamera için özel bir kural oluşturulmasını değerlendir.",
    technicalComponent: "Kamera kuralı",
    technicalType: "camera rule recommendation",
  },
  prompt_example: {
    category: "Analiz geliştirme",
    title: "Olay yorumlamasında tutarsızlık tespit edildi",
    description: "Benzer olaylarda sistem farklı yorumlar üretmiş olabilir.",
    action: "Analiz davranışının iyileştirilmesini değerlendir.",
    technicalComponent: "VLM",
    technicalType: "prompt example candidate",
  },
  threshold_calibration: {
    category: "Ayar önerisi",
    title: "Sistem güveni ile insan kararı birlikte değerlendirilmeli",
    description: "İnsan incelemesi, alarm hassasiyetinin yeniden kontrol edilmesini yararlı kılabilir.",
    action: "Alarm hassasiyetini yeniden değerlendir.",
    technicalComponent: "Karar eşiği",
    technicalType: "threshold calibration",
  },
  siglip_training: {
    category: "Sistem geliştirme",
    title: "Sistem bazı görüntüleri ayırt etmekte zorlanıyor",
    description: "İnsan incelemeleri ile sistem sonuçları arasında tekrar eden farklılıklar bulundu.",
    action: "Bu örnekleri sistem geliştirmesi için kullan.",
    technicalComponent: "SigLIP",
    technicalType: "semantic training candidate",
  },
  d_fine_training: {
    category: "Sistem geliştirme",
    title: "Bazı kişi veya nesneler eksik tespit edilmiş olabilir",
    description: "İnsan incelemelerinde sistem tespiti ile görüntü arasında tekrar eden farklar bulundu.",
    action: "Düzeltilmiş örnekleri sistem geliştirmesi için kullan.",
    technicalComponent: "D-FINE",
    technicalType: "detector training candidate",
  },
  evaluation: {
    category: "Kalite kontrolü",
    title: "Doğrulanmış olay gelecekteki kontrolleri güçlendirebilir",
    description: "İnsan kararı verilen bu olay, sonraki sistem değişikliklerini kontrol etmek için kullanılabilir.",
    action: "Bu örneği gelecek kalite kontrollerinde kullan.",
    technicalComponent: "Değerlendirme hattı",
    technicalType: "evaluation candidate",
  },
};

const PRIMARY_USE_ORDER: DevelopmentUse[] = [
  "camera_rule",
  "siglip_training",
  "d_fine_training",
  "prompt_example",
  "threshold_calibration",
  "evaluation",
];

const REVIEW_REQUIRED_PRESENTATION: OperatorDevelopmentPresentation = {
  category: "İnsan incelemesi",
  title: "Bu olay için insan kararı gerekiyor",
  description: "Sistem sonucu henüz bir operatör tarafından doğrulanmadı.",
  action: "Olayı inceleyin ve sistem sonucunu doğrulayın.",
  technicalComponent: "İnceleme akışı",
  technicalType: "review required",
};

export function presentationForUse(use: DevelopmentUse): OperatorDevelopmentPresentation {
  return DEVELOPMENT_PRESENTATION[use];
}

export function primaryPresentationForCandidate(
  candidate: LearningCandidateSummary,
): OperatorDevelopmentPresentation {
  const primaryUse = PRIMARY_USE_ORDER.find((use) => candidate.recommended_uses.includes(use));
  return primaryUse ? presentationForUse(primaryUse) : REVIEW_REQUIRED_PRESENTATION;
}

export function candidateNeedsRenewedApproval(candidate: LearningCandidateSummary): boolean {
  return candidate.blockers.some((blocker) => blocker.includes("izin yenilenmeli"));
}

export function candidateStatus(candidate: LearningCandidateSummary): string {
  if (candidate.recommended_uses.length === 0) return "İnceleme gerekiyor";
  if (candidateNeedsRenewedApproval(candidate)) return "Yeniden onay gerekiyor";
  if (
    candidate.recommended_uses.length > 0
    && candidate.ready_uses.length === candidate.recommended_uses.length
  ) {
    return "Hazır";
  }
  return "Onay gerekiyor";
}

export function candidateActionTitle(
  candidate: LearningCandidateSummary,
  eventLabel: string,
): string {
  const titleLabel = eventLabel
    ? eventLabel.charAt(0).toLocaleUpperCase("tr-TR") + eventLabel.slice(1)
    : eventLabel;
  if (candidate.recommended_uses.length === 0) {
    return `${titleLabel} kaydı inceleme bekliyor`;
  }
  if (candidateNeedsRenewedApproval(candidate)) {
    return `${titleLabel} kaydı yeniden onay bekliyor`;
  }
  if (candidate.ready_uses.length === candidate.recommended_uses.length) {
    return `${titleLabel} kaydı işleme hazır`;
  }
  if (candidate.recommended_uses.includes("camera_rule")) {
    return `${titleLabel} kaydı kamera ayarı için onay bekliyor`;
  }
  return `${titleLabel} kaydı sistem iyileştirmesi için onay bekliyor`;
}

export function candidateReviewReason(candidate: LearningCandidateSummary): string {
  if (candidate.recommended_uses.length === 0) {
    return "Sistem bu olay için henüz insan kararı bulunmadığı için inceleme istiyor.";
  }
  if (candidateNeedsRenewedApproval(candidate)) {
    return "İnceleme sonucu değiştiği için yeniden onay gerekiyor.";
  }
  if (candidate.blockers.length > 0) {
    return "Önerinin ilerlemesi için insan onayı gerekiyor.";
  }
  return "İnsan incelemesi ve gerekli onaylar tamamlandı.";
}

export function visibleDevelopmentSuggestions(
  summaries: LearningRouteSummary[],
): LearningRouteSummary[] {
  return summaries.filter((summary) => summary.recommended_count > 0);
}

export function approvalWaitingCount(
  pendingApprovalEvents: number,
  staleApprovalEvents: number,
): number {
  return pendingApprovalEvents + staleApprovalEvents;
}

export function operatorApprovalState(route: LearningRoute): string {
  if (route.ready) return "Hazır";
  const labels: Record<LearningRoute["approval_state"], string> = {
    review_required: "İnceleme gerekiyor",
    approval_required: "Onay gerekiyor",
    approved: "Onaylandı",
    not_approved: "Onay gerekiyor",
    rejected: "Onaylanmadı",
    revoked: "Onay geri alındı",
    stale: "Yeniden onay gerekiyor",
  };
  return labels[route.approval_state];
}

export function systemBehaviorLabel(
  state: "insufficient_data" | "stable" | "watch" | "drift",
): string {
  const labels = {
    insufficient_data: "Henüz yeterli veri yok",
    stable: "Sistem davranışı normal",
    watch: "Performans değişimi inceleniyor",
    drift: "Performans değişimi tespit edildi",
  } as const;
  return labels[state];
}
