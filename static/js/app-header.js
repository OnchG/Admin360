(function(){
    function updateClock(){
        const now=new Date();
        document.querySelectorAll('[data-system-date]').forEach(el=>{
            el.textContent=new Intl.DateTimeFormat('bg-BG',{day:'2-digit',month:'2-digit',year:'numeric'}).format(now);
        });
        document.querySelectorAll('[data-system-time]').forEach(el=>{
            el.textContent=new Intl.DateTimeFormat('bg-BG',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(now);
        });
    }
    updateClock();
    window.setInterval(updateClock,1000);
    document.addEventListener('click',function(event){
        document.querySelectorAll('details.system-profile[open]').forEach(details=>{
            if(!details.contains(event.target)) details.removeAttribute('open');
        });
    });
})();
