from django.urls import path

from . import views

urlpatterns = [
    path('', views.reports_index, name='reports-index'),
    path('profit-loss/', views.profit_loss, name='reports-profit-loss'),
    path('cogs-detail/', views.cogs_detail, name='reports-cogs-detail'),
    path('stock-on-hand/', views.stock_on_hand, name='reports-stock-on-hand'),
    path('aged-receivables/', views.aged_receivables, name='reports-aged-receivables'),
    path('audit-trail/', views.audit_trail, name='reports-audit-trail'),
    path('z-report/', views.z_report_list, name='reports-z-report'),
    path('z-report/<int:shift_id>/', views.z_report_detail, name='reports-z-report-detail'),
    path('z-report/<int:shift_id>/record-count/', views.shift_record_count, name='reports-shift-record-count'),
    path('daily-sales/', views.daily_sales, name='reports-daily-sales'),
    path('voids-log/', views.voids_log, name='reports-voids-log'),
    path('cash-drawer/', views.cash_drawer, name='reports-cash-drawer'),
    path('cash-drawer-flow/', views.cash_drawer_flow, name='reports-cash-drawer-flow'),
    path('stock-variance/', views.stock_variance, name='reports-stock-variance'),
    path('sales-by-channel/', views.sales_by_channel, name='reports-sales-by-channel'),
    path('online-sales/', views.online_sales, name='reports-online-sales'),
    path('menu-margin/', views.menu_margin, name='reports-menu-margin'),
    path('best-selling/', views.best_selling, name='reports-best-selling'),
    path('promotional-pairings/', views.promotional_pairings, name='reports-promotional-pairings'),
    path('accompaniment-popularity/', views.accompaniment_popularity, name='reports-accompaniment-popularity'),
    path('category-performance/', views.category_performance, name='reports-category-performance'),
    path('waste-analysis/', views.waste_analysis, name='reports-waste-analysis'),
    path('staff-meals-cost/', views.staff_meals_cost, name='reports-staff-meals-cost'),
    path('slow-movers/', views.slow_movers, name='reports-slow-movers'),
    path('recipe-cost-drift/', views.recipe_cost_drift, name='reports-recipe-cost-drift'),
    path('channel-margin/', views.channel_margin, name='reports-channel-margin'),
]
