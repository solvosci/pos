# Copyright 2018 GRAP - Sylvain LE GAL
# Copyright 2018 Tecnativa S.L. - David Vidal
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, models, fields
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    _inherit = 'pos.order'

    returned_order_id = fields.Many2one(
        comodel_name='pos.order',
        string='Returned Order',
        readonly=True,
    )

    returned_order_reference = fields.Char(
        related='returned_order_id.pos_reference',
        string='Reference of the returned Order')

    refund_order_ids = fields.One2many(
        comodel_name='pos.order',
        inverse_name='returned_order_id',
        string='Refund Orders',
        readonly=True,
    )

    refund_order_qty = fields.Integer(
        compute='_compute_refund_order_qty',
        string='Refund Orders Quantity',
    )

    is_returnable = fields.Boolean(
        compute='_compute_is_returnable',
    )

    @api.multi
    @api.depends('refund_order_ids')
    def _compute_refund_order_qty(self):
        for order in self:
            order.refund_order_qty = len(order.refund_order_ids)

    def _compute_is_returnable(self):
        for order in self:
            order.is_returnable = \
                order.amount_total >= 0 and \
                sum([l.get_qty_returnable() for l in order.lines]) > 0

    def _blank_refund(self, res):
        self.ensure_one()
        new_order = self.browse(res['res_id'])
        new_order.returned_order_id = self
        # Remove created lines and recreate and link Lines
        new_order.lines.unlink()
        return new_order

    @api.multi
    def action_view_refund_orders(self):
        self.ensure_one()

        action = self.env.ref('point_of_sale.action_pos_pos_form').read()[0]

        if self.refund_order_qty == 1:
            action['views'] = [
                (self.env.ref('point_of_sale.view_pos_pos_form').id, 'form')]
            action['res_id'] = self.refund_order_ids.ids[0]
        else:
            action['domain'] = [('id', 'in', self.refund_order_ids.ids)]
        return action

    """
    @api.multi
    def refund(self):
        return super(PosOrder, self.with_context(refund=True)).refund()
    """
    def refund(self):
        # Call super to use original refund algorithm (session management, ...)
        ctx = dict(self.env.context, do_not_check_negative_qty=True)
        res = super(PosOrder, self.with_context(ctx)).refund()
        new_order = self._blank_refund(res)
        for line in self.lines:
            qty = - line.max_returnable_qty([])
            if qty != 0:
                copy_line = line.copy()
                copy_line.write({
                    'order_id': new_order.id,
                    'returned_line_id': line.id,
                    'qty': qty,
                })
        return res

    """
    @api.multi
    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        self.ensure_one()
        order = super().copy(default=default)
        if self.env.context.get('refund', False):
            order.returned_order_id = self.id
        return order
    """

    @api.model
    def _prepare_filter_for_pos(self, pos_session_id):
        return [
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ]

    @api.model
    def _prepare_filter_query_for_pos(self, pos_session_id, query):
        return [
            '|', '|',
            ('name', 'ilike', query),
            ('pos_reference', 'ilike', query),
            ('partner_id.display_name', 'ilike', query),
        ]

    @api.model
    def _prepare_fields_for_pos_list(self):
        return [
            'name', 'pos_reference', 'partner_id', 'date_order',
            'amount_total', 'is_returnable',
        ]

    @api.model
    def search_done_orders_for_pos(self, query, pos_session_id):
        session_obj = self.env['pos.session']
        config = session_obj.browse(pos_session_id).config_id
        condition = self._prepare_filter_for_pos(pos_session_id)
        if not query:
            # Search only this POS orders
            condition += [('config_id', '=', config.id)]
        else:
            # Search globally by criteria
            condition += self._prepare_filter_query_for_pos(
                pos_session_id, query)
        field_names = self._prepare_fields_for_pos_list()
        return self.search_read(
            condition, field_names, limit=config.iface_load_done_order_max_qty)

    @api.multi
    def _prepare_done_order_for_pos(self):
        self.ensure_one()
        order_lines = []
        payment_lines = []
        for order_line in self.lines:
            order_line = self._prepare_done_order_line_for_pos(order_line)
            order_lines.append(order_line)
        for payment_line in self.statement_ids:
            payment_line = self._prepare_done_order_payment_for_pos(
                payment_line)
            payment_lines.append(payment_line)
        res = {
            'id': self.id,
            'date_order': self.date_order,
            'pos_reference': self.pos_reference,
            'name': self.name,
            'partner_id': self.partner_id.id,
            'fiscal_position': self.fiscal_position_id.id,
            'line_ids': order_lines,
            'statement_ids': payment_lines,
            'to_invoice': bool(self.invoice_id),
            'returned_order_id': self.returned_order_id.id,
            'returned_order_reference': self.returned_order_reference,
        }
        return res

    @api.multi
    def _prepare_done_order_line_for_pos(self, order_line):
        self.ensure_one()
        return {
            'id': order_line.id,
            'product_id': order_line.product_id.id,
            'qty': order_line.qty,
            'qty_returnable': order_line.get_qty_returnable(),
            'price_unit': order_line.price_unit,
            'discount': order_line.discount,
        }

    @api.multi
    def _prepare_done_order_payment_for_pos(self, payment_line):
        self.ensure_one()
        return {
            'journal_id': payment_line.journal_id.id,
            'amount': payment_line.amount,
        }

    @api.multi
    def load_done_order_for_pos(self):
        self.ensure_one()
        return self._prepare_done_order_for_pos()

    @api.model
    def _order_fields(self, ui_order):
        res = super()._order_fields(ui_order)
        res.update({
            'returned_order_id': ui_order.get('returned_order_id', False),
        })
        return res


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    returned_line_id = fields.Many2one(
        comodel_name='pos.order.line',
        string='Returned Order',
        readonly=True,
    )
    refund_line_ids = fields.One2many(
        comodel_name='pos.order.line',
        inverse_name='returned_line_id',
        string='Refund Lines',
        readonly=True,
    )

    def get_qty_returnable(self):
        # TODO computed field? The same as max_returnable_qty()?
        self.ensure_one()
        return self.qty + sum(self.refund_line_ids.mapped('qty'))

    @api.model
    def max_returnable_qty(self, ignored_line_ids):
        qty = self.qty
        for refund_line in self.refund_line_ids:
            if refund_line.id not in ignored_line_ids:
                qty += refund_line.qty
        return qty

    @api.constrains('returned_line_id', 'qty')
    def _check_return_qty(self):
        if self.env.context.get('do_not_check_negative_qty', False):
            return True
        for line in self:
            if line.returned_line_id and -line.qty > line.returned_line_id.qty:
                raise ValidationError(_(
                    "You can not return %d %s of %s because the original "
                    "Order line only mentions %d %s."
                ) % (-line.qty, line.product_id.uom_id.name,
                     line.product_id.name, line.returned_line_id.qty,
                     line.product_id.uom_id.name))
            if (line.returned_line_id and
                    -line.qty >
                    line.returned_line_id.max_returnable_qty([line.id])):
                raise ValidationError(_(
                    "You can not return %d %s of %s because some refunds"
                    " have already been done.\n Maximum quantity allowed :"
                    " %d %s."
                ) % (-line.qty, line.product_id.uom_id.name,
                     line.product_id.name,
                     line.returned_line_id.max_returnable_qty([line.id]),
                     line.product_id.uom_id.name))
            if (not line.returned_line_id and
                    line.qty < 0 and not
                    line.product_id.product_tmpl_id.pos_allow_negative_qty):
                raise ValidationError(_(
                    "For legal and traceability reasons, you can not set a"
                    " negative quantity (%d %s of %s), without using "
                    "return wizard."
                ) % (line.qty, line.product_id.uom_id.name,
                     line.product_id.name))
