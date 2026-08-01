"""ESC/POS thermal-receipt rendering for the POS.

Builds a *raw* ESC/POS byte stream for a generic POS-80 printer (80 mm roll,
48 characters per line at Font A, code page cp437). The bytes are meant to be
sent to the Windows "POS-80" print queue in RAW datatype — the only method
proven to work on these printers — via the QZ Tray bridge on each register.

Public entry point:

    build_receipt(order, *, width=48) -> bytes

The stream always starts with an ``ESC @`` init, selects cp437, ends with a
partial cut (``GS V 66 0``) and a trailing ``ESC @`` re-init. The trailing
re-init is deliberate: without it the *next* receipt on the same spooler
connection loses its formatting (a known desync on these generic units).

Nothing here is shared/global — every call builds a fresh ``bytearray``.

A configured restaurant logo is converted to a monochrome ``GS v 0`` raster
image before it is prepended to the receipt. If the image cannot be read, the
receipt still prints without it.
"""

from __future__ import annotations

from decimal import Decimal

# ── Raw ESC/POS control codes ───────────────────────────────────────────────
ESC = b"\x1b"
GS = b"\x1d"

INIT = ESC + b"@"                 # ESC @  — initialise / clear formatting
CODEPAGE_CP437 = ESC + b"t\x00"   # ESC t 0 — select character code table cp437

ALIGN_LEFT = ESC + b"a\x00"
ALIGN_CENTER = ESC + b"a\x01"
ALIGN_RIGHT = ESC + b"a\x02"

BOLD_ON = ESC + b"E\x01"
BOLD_OFF = ESC + b"E\x00"

# GS ! n — character size. Low nibble = height mult, high nibble = width mult.
SIZE_NORMAL = GS + b"!\x00"
SIZE_DOUBLE = GS + b"!\x11"        # double width + double height

# GS V 66 0 — partial cut (function B, feed 0). Spelled out per spec.
PARTIAL_CUT = GS + b"V" + bytes([66, 0])

DEFAULT_WIDTH = 48                 # chars/line for POS-80 at Font A

# Unicode → cp437-safe ASCII fallbacks for glyphs the app commonly emits.
_CP437_FALLBACKS = {
    "—": "-", "–": "-", "−": "-",
    "·": "*", "•": "*",
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "…": "...",
    "½": "1/2", "¼": "1/4", "¾": "3/4",
    "₹": "Rs", "€": "EUR", "£": "GBP",
}


class _Receipt:
    """Accumulates an ESC/POS byte stream. One instance per receipt."""

    def __init__(self, width: int = DEFAULT_WIDTH):
        self.width = width
        self.buf = bytearray()

    # -- low level ----------------------------------------------------------
    def raw(self, data: bytes) -> "_Receipt":
        self.buf.extend(data)
        return self

    def text(self, s: str) -> "_Receipt":
        """Append text, mapping unknown glyphs to cp437-safe ASCII."""
        for uni, repl in _CP437_FALLBACKS.items():
            if uni in s:
                s = s.replace(uni, repl)
        self.buf.extend(s.encode("cp437", "replace"))
        return self

    def line(self, s: str = "") -> "_Receipt":
        return self.text(s).raw(b"\n")

    def feed(self, n: int = 1) -> "_Receipt":
        self.buf.extend(ESC + b"d" + bytes([max(0, min(n, 255))]))
        return self

    def rule(self, ch: str = "-") -> "_Receipt":
        return self.line(ch * self.width)

    # -- two-column rows ----------------------------------------------------
    def row(self, left: str, right: str) -> "_Receipt":
        """One line: ``left`` flush-left, ``right`` flush-right, padded to width.

        If the two collide, the left text is truncated so the amount is never
        lost or pushed onto a second line.
        """
        right = right or ""
        max_left = self.width - len(right) - 1
        if max_left < 1:                      # pathologically long amount
            return self.line(right[: self.width])
        if len(left) > max_left:
            left = left[: max_left - 1] + "."  # exact-width truncation marker
        gap = self.width - len(left) - len(right)
        return self.line(left + (" " * gap) + right)

    def item_row(self, left: str, right: str) -> "_Receipt":
        """Like :meth:`row` but wraps a long left label across lines, keeping
        the amount on the first line and continuation text underneath."""
        right = right or ""
        first_width = self.width - len(right) - 1
        if first_width < 1:
            return self.line(left).line(right)
        words = left.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip()
            limit = first_width if not lines else self.width
            if len(cand) <= limit:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if not lines:
            lines = [""]
        # first line carries the amount
        head = lines[0]
        gap = self.width - len(head) - len(right)
        self.line(head + (" " * max(1, gap)) + right)
        for cont in lines[1:]:
            self.line(cont)
        return self

    def bytes(self) -> bytes:
        return bytes(self.buf)


def _money(amount, symbol: str = "") -> str:
    """Format a Decimal/number as ``1,234.50`` with an optional prefix."""
    if amount is None:
        amount = Decimal("0")
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return f"{symbol}{amount:,.2f}"


def _logo_raster(logo, *, max_width: int, max_height: int) -> bytes | None:
    """Convert a Django image field into a centred ESC/POS ``GS v 0`` raster.

    ESC/POS printers do not understand PNG/JPEG files sent as RAW data. They
    require one bit per pixel, packed left-to-right in rows. Alpha is composed
    onto white so transparent logos do not print as a black rectangle.
    """
    if not logo:
        return None

    try:
        from PIL import Image, ImageOps

        logo.open('rb')
        with Image.open(logo) as source:
            source.load()
            if source.mode in ('RGBA', 'LA') or (
                source.mode == 'P' and 'transparency' in source.info
            ):
                rgba = source.convert('RGBA')
                image = Image.new('RGB', rgba.size, 'white')
                image.paste(rgba, mask=rgba.getchannel('A'))
            else:
                image = source.convert('RGB')

        image.thumbnail((max(1, max_width), max(1, max_height)), Image.Resampling.LANCZOS)
        grayscale = ImageOps.grayscale(image)
        # A threshold preserves crisp text/marks and avoids the muddy output
        # that dithering often produces on low-cost thermal printers.
        bitmap = grayscale.point(lambda value: 0 if value < 180 else 255, mode='1')
        pixel_width, height = bitmap.size
        bytes_per_row = (pixel_width + 7) // 8
        data = bytearray(bytes_per_row * height)

        for y in range(height):
            row_start = y * bytes_per_row
            for x in range(pixel_width):
                if bitmap.getpixel((x, y)) == 0:
                    data[row_start + (x // 8)] |= 0x80 >> (x % 8)

        # GS v 0 m xL xH yL yH d1...dk — mode 0 is normal density.
        return GS + b'v0' + bytes([
            0,
            bytes_per_row & 0xFF, (bytes_per_row >> 8) & 0xFF,
            height & 0xFF, (height >> 8) & 0xFF,
        ]) + bytes(data)
    except Exception:
        # A logo should never prevent an order receipt from being printed.
        return None
    finally:
        try:
            logo.close()
        except Exception:
            pass


def build_receipt(order, *, width: int = DEFAULT_WIDTH) -> bytes:
    """Render ``order`` into a raw ESC/POS byte stream for a POS-80 printer.

    :param order: a ``menu.models.Order`` instance.
    :param width: characters per line (48 for POS-80 Font A).
    :returns: raw ``bytes`` ready to hand to the printer in RAW datatype.
    """
    from .models import RestaurantSettings

    shop = RestaurantSettings.load()
    sym = shop.currency_symbol
    r = _Receipt(width)

    from django.conf import settings
    logo_bytes = _logo_raster(
        shop.logo,
        max_width=getattr(settings, 'POS_RECEIPT_LOGO_MAX_WIDTH', 384),
        max_height=getattr(settings, 'POS_RECEIPT_LOGO_MAX_HEIGHT', 160),
    )

    # Fresh init every time, then select cp437 so box/extended glyphs render.
    r.raw(INIT).raw(CODEPAGE_CP437)

    # ── Logo (raster) ──
    if logo_bytes:
        r.raw(ALIGN_CENTER).raw(logo_bytes).raw(b"\n").raw(ALIGN_LEFT)

    # ── Header ──
    r.raw(ALIGN_CENTER)
    r.raw(BOLD_ON).raw(SIZE_DOUBLE).line(shop.name).raw(SIZE_NORMAL).raw(BOLD_OFF)
    if shop.tagline:
        r.line(shop.tagline)
    if shop.address:
        for ln in str(shop.address).splitlines():
            if ln.strip():
                r.line(ln.strip())
    if shop.phone:
        r.line(f"Tel: {shop.phone}")
    if shop.tax_number:
        r.line(f"PIN: {shop.tax_number}")
    r.raw(ALIGN_LEFT)

    # ── Status banner (unpaid / void) ──
    if order.status == "active":
        r.raw(ALIGN_CENTER).raw(BOLD_ON)
        r.line("*** NOT PAID - PROFORMA ***")
        r.raw(BOLD_OFF).line("Payment not yet received").raw(ALIGN_LEFT)
    elif order.status == "cancelled":
        r.raw(ALIGN_CENTER).raw(BOLD_ON)
        r.line("*** VOID - NOT A VALID RECEIPT ***")
        r.raw(BOLD_OFF).line("This order was cancelled").raw(ALIGN_LEFT)

    r.rule()

    # ── Order meta ──
    created = order.created_at
    r.row(f"Order #{order.id}", created.strftime("%d/%m/%Y") if created else "")
    if order.order_type == "dine_in":
        left = str(order.table) if order.table else "Dine-in"
    else:
        left = order.get_order_type_display()
        if order.source != "pos":
            left += f" ({order.get_source_display()})"
    r.row(left, created.strftime("%H:%M") if created else "")
    if order.waiter:
        r.line(f"Served by: {(order.waiter.get_full_name() or order.waiter.username).title()}")
    if order.created_by:
        name = order.created_by.get_full_name() or order.created_by.username
        r.line(f"Created by: {name.title()} (Marketing)")

    r.rule()

    # ── Line items ──
    r.raw(BOLD_ON).row("Item", "Amount").raw(BOLD_OFF)
    for item in order.items.all():
        title = item.menu_item.title if item.menu_item else "(item)"
        r.item_row(f"{item.quantity}x {title}", _money(item.get_subtotal()))
        for opt in item.options.all():
            extra = ""
            if opt.price_delta and opt.price_delta > 0:
                extra = f" (+{_money(opt.price_delta, sym)})"
            r.line(f"  + {opt.label}{extra}")
        if item.notes:
            r.line(f"  {item.notes}")

    r.rule()

    # ── Totals ──
    subtotal = order.get_subtotal()
    total = order.get_total()
    if order.is_comp or (order.discount_amount and order.discount_amount > 0):
        r.row("Subtotal", _money(subtotal, sym))
        if order.is_comp:
            r.row("Complimentary", "-" + _money(subtotal, sym))
        elif order.discount_amount and order.discount_amount > 0:
            r.row("Discount", "-" + _money(order.discount_amount, sym))

    r.raw(BOLD_ON)
    r.row("TOTAL (COMP)" if order.is_comp else "TOTAL", _money(total, sym))
    r.raw(BOLD_OFF)
    # ── Payment ──
    if order.payment_method:
        r.text("\n").row("Payment", order.get_payment_method_display())
        if order.payment_method == "split":
            for method, amount in order.payment_breakdown().items():
                label = dict(order.PAYMENT_CHOICES).get(method, method)
                r.row(f"  {label}", _money(amount, sym))
        if order.mpesa_code:
            r.row("M-Pesa Code", order.mpesa_code)
        r.rule()

    # ── M-Pesa Buy Goods till ──
    if shop.mpesa_till_number:
        r.raw(ALIGN_CENTER).raw(BOLD_ON)
        r.line("M-PESA - BUY GOODS")
        r.raw(SIZE_DOUBLE).line(str(shop.mpesa_till_number)).raw(SIZE_NORMAL)
        r.raw(BOLD_OFF).raw(ALIGN_LEFT)

    # ── Footer ──
    r.raw(ALIGN_CENTER)
    r.feed(1).line("Thank you for dining with us!")
    if shop.website:
        r.line(str(shop.website))
    r.raw(ALIGN_LEFT)

    # ── Cut, then re-init so the NEXT receipt isn't desynced ──
    r.feed(3).raw(PARTIAL_CUT).raw(INIT)

    return r.bytes()
