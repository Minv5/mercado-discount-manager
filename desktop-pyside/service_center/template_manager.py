from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from engine.crypto import get_data_dir


@dataclass
class ReplyTemplate:
    id: str
    title: str
    category: str  # "pre_sale" | "after_sale" | "claim" | "general"
    text_es: str
    text_pt: str
    text_zh: str


DEFAULT_TEMPLATES: list[ReplyTemplate] = [
    ReplyTemplate(
        id="tpl_stock_yes",
        title="有现货 (即刻发货)",
        category="pre_sale",
        text_zh="亲，商品现货充足，下单后我们会第一时间为您安排包装出库，欢迎选购！",
        text_es="¡Hola! Sí, tenemos stock disponible. Una vez realizada su compra, prepararemos su paquete de inmediato. ¡Esperamos su compra!",
        text_pt="Olá! Sim, temos estoque disponível. Assim que sua compra for concluída, despacharemos seu pedido imediatamente. Aguardamos sua compra!",
    ),
    ReplyTemplate(
        id="tpl_shipping_time",
        title="时效说明 (跨境直发)",
        category="pre_sale",
        text_zh="亲，本店为官方跨境直发，全程带物流追踪，预计将在预计送达时间内安全送达您手中。",
        text_es="¡Hola! Nuestro envío es internacional con seguimiento oficial en todo momento. Llegará de forma segura dentro de la fecha estimada por Mercado Libre.",
        text_pt="Olá! Nosso envio é internacional com rastreamento oficial em todo o trajeto. Chegará com segurança dentro do prazo estimado pelo Mercado Livre.",
    ),
    ReplyTemplate(
        id="tpl_wrong_item_resend",
        title="发错货安抚 (免费补发/退款)",
        category="after_sale",
        text_zh="亲，非常抱歉给您带来不便！我们核实确实是仓库包装失误，我们可以为您立即安排免费补发或全额退款，请问您倾向哪种方案？",
        text_es="Estimado cliente, ¡lamentamos sinceramente las molestias! Verificamos que hubo un error en nuestro empaque. Podemos gestionar un reenvío gratuito de inmediato o el reembolso total. ¿Qué opción prefiere?",
        text_pt="Prezado cliente, pedimos sinceras desculpas pelo transtorno! Confirmamos que houve um erro em nosso empacotamento. Podemos providenciar o reenvio gratuito imediato ou o reembolso total. Qual opção você prefere?",
    ),
    ReplyTemplate(
        id="tpl_claim_apology",
        title="索赔沟通 (协助处理退货)",
        category="claim",
        text_zh="亲，我们已关注到您的索赔申请，我们将全力配合为您解决，请您放心，会严格遵循美客多平台政策保障您的权益。",
        text_es="Estimado comprador, hemos recibido su reclamo y estamos totalmente a su disposición para resolverlo rápidamente conforme a las políticas de Compra Protegida de Mercado Libre.",
        text_pt="Prezado comprador, recebemos sua reclamação e estamos totalmente à disposição para resolver o mais rápido possível, de acordo com as políticas do Mercado Livre.",
    ),
    ReplyTemplate(
        id="tpl_thank_you",
        title="感谢选购 (祝生活愉快)",
        category="general",
        text_zh="非常感谢您的信任与支持！如果您有任何问题随时联系我们，祝您生活愉快！",
        text_es="¡Muchas gracias por su compra y confianza! Si tiene alguna otra duda, estamos a su servicio. ¡Que tenga un excelente día!",
        text_pt="Muito obrigado por sua compra e confiança! Se tiver qualquer dúvida, estamos à sua disposição. Tenha um excelente dia!",
    ),
]


class TemplateManager:
    """Manages custom and pre-seeded quick reply templates."""

    def __init__(self, storage_path: Path | str | None = None):
        self.storage_path = Path(storage_path) if storage_path else (get_data_dir() / "customer_service_templates.json")
        self._templates: list[ReplyTemplate] = []
        self.load()

    def load(self) -> None:
        if not self.storage_path.exists():
            self._templates = list(DEFAULT_TEMPLATES)
            self.save()
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._templates = [ReplyTemplate(**item) for item in data]
            else:
                self._templates = list(DEFAULT_TEMPLATES)
        except Exception:
            self._templates = list(DEFAULT_TEMPLATES)

    def save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        raw = [asdict(t) for t in self._templates]
        self.storage_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_templates(self, category: str | None = None) -> list[ReplyTemplate]:
        if not category or category == "all":
            return list(self._templates)
        return [t for t in self._templates if t.category == category or t.category == "general"]

    def add_template(self, template: ReplyTemplate) -> None:
        self._templates.append(template)
        self.save()

    def remove_template(self, template_id: str) -> None:
        self._templates = [t for t in self._templates if t.id != template_id]
        self.save()

    def get_template(self, template_id: str) -> ReplyTemplate | None:
        for t in self._templates:
            if t.id == template_id:
                return t
        return None
