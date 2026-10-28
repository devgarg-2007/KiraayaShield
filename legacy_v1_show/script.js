function generatePlan() {
    const btn = document.getElementById('actionBtn');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Generating Document...';
    btn.classList.add('opacity-80', 'cursor-not-allowed');
    
    setTimeout(() => {
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Plan Ready to Download';
        btn.classList.remove('bg-slate-900', 'hover:bg-slate-800');
        btn.classList.add('bg-emerald-600', 'hover:bg-emerald-700');
        
        alert("Success! KiraayaShield has compiled your evidence and legal precedents into a formal complaint draft.");
    }, 2000);
}
