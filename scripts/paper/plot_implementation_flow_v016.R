library(ggplot2)
args <- commandArgs(trailingOnly=TRUE)
d <- read.csv(args[1])
out <- args[2]
dir.create(out, recursive=TRUE, showWarnings=FALSE)
spec <- list(
 source38=list(panel='Study 38: first raw cycle (s)',colour='#779FB2',x='Time (s)',median=FALSE),
 source120=list(panel='Gait120: first released envelope (sample)',colour='#B78D62',x='Sample',median=FALSE),
 median38=list(panel='Study 38: five-cycle median',colour='#779FB2',x='Cycle (%)',median=TRUE),
 median120=list(panel='Gait120: ten-cycle median',colour='#B78D62',x='Cycle (%)',median=TRUE))
for (name in names(spec)) {
 s <- spec[[name]]
 z <- subset(d,panel==s$panel)
 stopifnot(nrow(z)>0)
 p <- ggplot()
 if (s$median) p <- p + geom_line(data=subset(z,curve!='median'),aes(x,value,group=curve),colour='grey82',linewidth=.14)
 front <- subset(z,curve==if(s$median)'median' else 'source')
 p <- p + geom_line(data=front,aes(x,value),colour=s$colour,linewidth=if(s$median).35 else .16)+
  labs(x=s$x,y=NULL)+expand_limits(y=0)+
  scale_y_continuous(breaks=if(s$median)c(0,1) else scales::breaks_pretty(n=2),expand=expansion(mult=.05))+
  scale_x_continuous(breaks=range(front$x),labels=scales::label_number(accuracy=if(name=='source38').01 else 1),expand=expansion(mult=c(.02,.02)))+
  theme_classic(base_size=6.5,base_family='Helvetica')+
  theme(axis.title=element_text(size=6.5,margin=margin(t=0)),axis.text=element_text(size=6.5,colour='black'),
        axis.ticks.length=grid::unit(.6,'mm'),axis.line=element_line(linewidth=.17),plot.margin=margin(1,2,1,1))
 ggsave(file.path(out,paste0(name,'.pdf')),p,width=28,height=27,units='mm',device='pdf',useDingbats=FALSE)
}
