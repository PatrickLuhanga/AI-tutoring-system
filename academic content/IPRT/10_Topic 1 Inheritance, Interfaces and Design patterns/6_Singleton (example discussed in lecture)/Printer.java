package singleton;

public class Printer {
	private static Printer p;
	private String name;
	
	private Printer() {
		
	}
	
	
	
	public Printer(String name) {
		this.name = name;
	}



	public synchronized static Printer getInstance() {
		if(p==null) {
			p=new Printer();
		}		
		return p;
		
	}
	public synchronized static Printer getInstance(String name) {
		if(p==null) {
			p=new Printer(name);
		}		
		return p;
		
	}



	@Override
	public String toString() {
		return "Printer [name=" + name + "]";
	}
	
	
}
