// --- FINANCIAL ANALYTICS CHARTING ENGINE ---

function initAnalyticsCharts(childId) {
    let url = "/api/analytics_data";
    if (childId) {
        url += "?child_id=" + childId;
    }

    fetch(url)
        .then(res => res.json())
        .then(data => {
            // Update Summary Card elements on the Analytics page
            const summaryStreak = document.getElementById("summary-streak");
            const summaryXp = document.getElementById("summary-xp");
            
            if (summaryStreak) {
                summaryStreak.textContent = `${data.streak} Days 🔥`;
            }
            if (summaryXp) {
                const levelTitles = {
                    1: "Beginner Saver",
                    2: "Smart Saver",
                    3: "Money Expert",
                    4: "Budget Master",
                    5: "Finance Champion"
                };
                const title = levelTitles[data.level] || "Saver";
                summaryXp.textContent = `Level ${data.level} (${title}) - ${data.xp} XP`;
            }

            // Global Chart Options
            const chartFont = {
                family: "'Plus Jakarta Sans', sans-serif",
                size: 11
            };
            const chartGridColor = "rgba(255, 255, 255, 0.05)";
            const chartLabelColor = "rgba(255, 255, 255, 0.6)";

            // 1. PIE CHART: Category Spending
            const ctxPie = document.getElementById('categoryPieChart');
            if (ctxPie) {
                const categories = data.spending_categories.map(c => c.category);
                const totals = data.spending_categories.map(c => c.total);

                const categoryColors = {
                    'Food': '#ff5f1f',
                    'Books': '#00f2fe',
                    'Travel': '#8a2be2',
                    'Entertainment': '#ff007f',
                    'Shopping': '#ffd700',
                    'Others': '#a0a0b0'
                };
                const colors = categories.map(cat => categoryColors[cat] || '#ffffff');

                new Chart(ctxPie, {
                    type: 'doughnut',
                    data: {
                        labels: categories,
                        datasets: [{
                            data: totals,
                            backgroundColor: colors,
                            borderColor: '#0f101a',
                            borderWidth: 2,
                            hoverOffset: 15
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'bottom',
                                labels: {
                                    color: '#fff',
                                    font: chartFont,
                                    padding: 15
                                }
                            }
                        }
                    }
                });
            }

            // 2. BAR CHART: Monthly Spending
            const ctxBar = document.getElementById('monthlySpendingBarChart');
            if (ctxBar) {
                const months = data.monthly_spending.map(m => m.month);
                const barTotals = data.monthly_spending.map(m => m.total);

                // Set up gradient for bar
                const canvasCtx = ctxBar.getContext('2d');
                const gradient = canvasCtx.createLinearGradient(0, 0, 0, 300);
                gradient.addColorStop(0, '#ff007f');
                gradient.addColorStop(1, '#8a2be2');

                new Chart(ctxBar, {
                    type: 'bar',
                    data: {
                        labels: months,
                        datasets: [{
                            label: 'Spent (₹)',
                            data: barTotals,
                            backgroundColor: gradient,
                            borderColor: 'transparent',
                            borderRadius: 6,
                            maxBarThickness: 45
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                grid: { color: chartGridColor },
                                ticks: { color: chartLabelColor, font: chartFont }
                            },
                            x: {
                                grid: { display: false },
                                ticks: { color: chartLabelColor, font: chartFont }
                            }
                        }
                    }
                });
            }

            // 3. LINE CHART: Savings Growth
            const ctxLine = document.getElementById('savingsGrowthLineChart');
            if (ctxLine) {
                const dates = data.savings_growth.labels;
                const savingsValues = data.savings_growth.data;

                const canvasCtx = ctxLine.getContext('2d');
                const gradient = canvasCtx.createLinearGradient(0, 0, 0, 300);
                gradient.addColorStop(0, 'rgba(0, 242, 254, 0.25)');
                gradient.addColorStop(1, 'rgba(0, 242, 254, 0)');

                new Chart(ctxLine, {
                    type: 'line',
                    data: {
                        labels: dates,
                        datasets: [{
                            label: 'Total Saved (₹)',
                            data: savingsValues,
                            borderColor: '#00f2fe',
                            borderWidth: 3,
                            backgroundColor: gradient,
                            fill: true,
                            tension: 0.3,
                            pointBackgroundColor: '#8a2be2',
                            pointBorderColor: '#00f2fe',
                            pointHoverRadius: 7
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                grid: { color: chartGridColor },
                                ticks: { color: chartLabelColor, font: chartFont }
                            },
                            x: {
                                grid: { color: chartGridColor },
                                ticks: { color: chartLabelColor, font: chartFont }
                            }
                        }
                    }
                });
            }

            // 4. COMPARISON CHART: Goals target vs actual saved
            const ctxGoalComp = document.getElementById('goalsComparisonChart');
            if (ctxGoalComp) {
                const goalNames = data.goals.map(g => g.goal_name);
                const targets = data.goals.map(g => g.target_amount);
                const saveds = data.goals.map(g => g.saved_amount);

                new Chart(ctxGoalComp, {
                    type: 'bar',
                    data: {
                        labels: goalNames,
                        datasets: [
                            {
                                label: 'Goal Target (₹)',
                                data: targets,
                                backgroundColor: 'rgba(255, 255, 255, 0.1)',
                                borderColor: 'rgba(255, 255, 255, 0.2)',
                                borderWidth: 1,
                                borderRadius: 5
                            },
                            {
                                label: 'Saved Amount (₹)',
                                data: saveds,
                                backgroundColor: '#39ff14',
                                borderColor: 'transparent',
                                borderRadius: 5
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                labels: { color: '#fff', font: chartFont }
                            }
                        },
                        scales: {
                            y: {
                                grid: { color: chartGridColor },
                                ticks: { color: chartLabelColor, font: chartFont }
                            },
                            x: {
                                grid: { display: false },
                                ticks: { color: chartLabelColor, font: chartFont }
                            }
                        }
                    }
                });
            }
        })
        .catch(err => {
            console.error("Error drawing charts: ", err);
        });
}

// --- DYNAMIC GAMIFICATION TOAST NOTIFICATIONS ---

function createBadgeToast(badgeName) {
    const container = document.getElementById('badge-popups');
    if (!container) return;

    const toast = document.createElement('div');
    toast.classList.add('badge-toast');

    const emoji = badgeName.split(' ')[0] || '🏆';
    const name = badgeName.split(' ').slice(1).join(' ') || 'Achievement Unlocked';

    toast.innerHTML = `
        <div style="font-size: 2.2rem;">${emoji}</div>
        <div>
            <div style="font-family: 'Outfit', sans-serif; font-weight: 700; font-size: 0.95rem; color: #ffd700; margin-bottom: 2px;">BADGE UNLOCKED!</div>
            <div style="font-size: 0.85rem; color: #fff; font-weight: 600;">${name}</div>
        </div>
    `;

    container.appendChild(toast);

    // Auto delete from DOM after animations complete
    setTimeout(() => {
        toast.remove();
    }, 5000);
}
