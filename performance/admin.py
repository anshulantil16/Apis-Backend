from django.contrib import admin
from .models import (
    EmployeeProfile, PerformanceCycle, GoalCard,
    Goal, QuarterlyReview, ApprovalLog
)

admin.site.register(EmployeeProfile)
admin.site.register(PerformanceCycle)
admin.site.register(GoalCard)
admin.site.register(Goal)
admin.site.register(QuarterlyReview)
admin.site.register(ApprovalLog)
