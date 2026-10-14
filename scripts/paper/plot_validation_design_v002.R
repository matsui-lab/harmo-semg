library(ggplot2)
args <- commandArgs(trailingOnly=TRUE)
d <- read.csv(args[1])
p <- ggplot(d, aes(phase_percent, study38_subject1)) +
  annotate('rect', xmin=c(0,60), xmax=c(20,100), ymin=-Inf, ymax=Inf,
           fill='#A8CADB', alpha=.28) +
  geom_line(linewidth=.45, colour='#111111') +
  geom_point(data=subset(d,phase_percent==63),size=1.5) +
  annotate('text',x=63,y=1.04,label='Peak: 63%',size=2.8,hjust=.5) +
  scale_x_continuous(breaks=c(0,20,60,100),expand=expansion(mult=c(0,.04))) +
  scale_y_continuous(breaks=c(0,1),limits=c(0,1.15),expand=expansion(mult=0)) +
  labs(x='Gait cycle (%)',y='Scaled EMG') +
  theme_classic(base_size=8,base_family='Helvetica') +
  theme(axis.text=element_text(colour='black'),axis.line=element_line(linewidth=.2),
        plot.margin=margin(2,7,1,1),axis.title=element_text(size=8))
ggsave(args[2],p,width=76,height=34,units='mm',device='pdf',useDingbats=FALSE)
