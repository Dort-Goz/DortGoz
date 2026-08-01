"""[1] ALGI — D-FINE + BYTE + RTMPose (CPU, ONNX Runtime).

Lisans politikası: Ultralytics/boxmot (AGPL) KULLANILMAZ.
  - Dedektör: D-FINE-S/M (Apache-2.0), resmî ONNX dışa aktarım
  - Takip: BYTE algoritması — supervision (MIT) üzerinden
  - Poz: RTMPose-m (Apache-2.0, MMDeploy ONNX)

TODO(hafta 2): detect(frame) → list[BoundingBox]
TODO(hafta 2): track(detections) → iz kimlikli kutular
TODO(hafta 2): pose(person_boxes) → düşme/hareketsizlik sinyalleri
TODO(hafta 2): event_candidates(tracks, poses, motion) → olay adayları
"""
